"""Optional scheduled DRAFT refresh over *synthetic-only* roster data.

This module cannot obtain or infer real identities. It never auto-publishes,
and requires explicit opt-in plus a running scheduler process. The SQLite
transaction serializes actual changes across processes.
"""
from datetime import datetime,timedelta,timezone
from pydantic import BaseModel,Field
from typing import Literal


class Policy(BaseModel):
    enabled: bool = False
    interval_hours: int = Field(default=24,ge=1,le=720)
    days: int = Field(default=7,ge=1,le=365)
    strategy: Literal['earliest','frequency','manual'] = 'earliest'
    correction_mode: Literal['rebuild','reconcile'] = 'reconcile'


def initialize(c):
    c.executescript("""CREATE TABLE IF NOT EXISTS synthetic_registry_maintenance (
        id INTEGER PRIMARY KEY CHECK(id=1),
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
        interval_hours INTEGER NOT NULL DEFAULT 24,
        days INTEGER NOT NULL DEFAULT 7,
        strategy TEXT NOT NULL DEFAULT 'earliest',
        correction_mode TEXT NOT NULL DEFAULT 'reconcile',
        last_checked_at TEXT
    );""")
    c.execute("INSERT OR IGNORE INTO synthetic_registry_maintenance(id) VALUES(1)")


def configuration(c):
    row=c.execute('SELECT * FROM synthetic_registry_maintenance WHERE id=1').fetchone()
    return dict(row)


def configure(c,policy:Policy):
    c.execute("""UPDATE synthetic_registry_maintenance
        SET enabled=?,interval_hours=?,days=?,strategy=?,correction_mode=?,last_checked_at=NULL
        WHERE id=1""",(int(policy.enabled),policy.interval_hours,policy.days,
                       policy.strategy,policy.correction_mode))
    return configuration(c)


def tick(c,*,now:datetime|None=None,force:bool=False):
    """Return a decision with evidence. Only create draft when inputs changed.

    A periodic job NEVER publishes; humans still review simulated conflicts.
    Newer observations outside a published window can cause a new draft,
    without changing the published historical snapshot.
    """
    from app import base_registry
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current=configuration(c)
    if not current['enabled'] and not force:
        return {'action':'disabled','policy':current}
    checked=current['last_checked_at']
    if checked and not force and now<datetime.fromisoformat(checked)+timedelta(hours=current['interval_hours']):
        return {'action':'not_due','policy':current}
    observations=[r for r in base_registry._effective_rows(c) if not r['excluded']]
    if not observations:
        action={'action':'no_source_observations'}
    else:
        latest_time=max(r['observed_at'] for r in observations)
        start=(datetime.fromisoformat(latest_time)-timedelta(days=current['days'])).isoformat()
        scoped=base_registry._window_rows(observations,start,latest_time)
        signature=base_registry._data_fingerprint(scoped)
        latest=base_registry._latest(c)
        pub=base_registry._published(c)
        # Existing draft for these exact inputs is reusable, no duplicate.
        if latest and latest['state']=='draft' and latest['input_signature']==signature and (
            latest['window_start']==start and latest['window_end']==latest_time):
            action={'action':'draft_exists','run_id':latest['id']}
        elif pub and pub['input_signature']==signature and pub['window_start']==start and (
              pub['window_end']==latest_time):
            action={'action':'unchanged','run_id':pub['id']}
        else:
            gen=base_registry.generate(c,base_registry.GenerateIn(
                days=current['days'],strategy=current['strategy'],
                correction_mode=current['correction_mode']))
            action={'action':'draft_created','run_id':gen['run']['id'],
                    'unresolved_count':gen['unresolved_count'],
                    'input_signature':gen['run']['input_signature']}
    c.execute("UPDATE synthetic_registry_maintenance SET last_checked_at=? WHERE id=1",
              (now.isoformat(),))
    c.execute("INSERT INTO synthetic_registry_audit(operation,run_id,detail,created_at) "
              "VALUES(?,?,?,?)",('maintenance_tick',action.get('run_id') or 0,
                                 action['action'],now.isoformat()))
    return {**action,'policy':configuration(c)}
