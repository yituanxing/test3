"""Synthetic-only place roster snapshots and human corrections.

No live Frigate events, camera frame, embeddings, or real identifiers enter
this module. A generated roster is a SIMULATION, not evidence of identity.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from app.occurrence_semantics import appearance_count
from app import roster_maintenance


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def initialize(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS synthetic_registry_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL CHECK(strategy IN ('earliest','frequency','manual')),
            correction_mode TEXT NOT NULL CHECK(correction_mode IN ('rebuild','reconcile')),
            days INTEGER NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            input_signature TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('draft','published','archived')),
            created_at TEXT NOT NULL, published_at TEXT,
            parent_run_id INTEGER REFERENCES synthetic_registry_runs(id)
        );
        CREATE TABLE IF NOT EXISTS synthetic_registry_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES synthetic_registry_runs(id),
            person TEXT NOT NULL, place_id INTEGER NOT NULL REFERENCES places(id),
            first_seen TEXT NOT NULL, appearances INTEGER NOT NULL,
            raw_observations INTEGER NOT NULL DEFAULT 0,
            visits INTEGER NOT NULL, observation_ids TEXT NOT NULL,
            UNIQUE(run_id,person,place_id)
        );
        CREATE TABLE IF NOT EXISTS synthetic_registry_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES synthetic_registry_runs(id),
            person TEXT NOT NULL, place_id INTEGER REFERENCES places(id),
            action TEXT NOT NULL CHECK(action IN ('assign','remove')),
            reason TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS registry_decisions_by_run_person
            ON synthetic_registry_decisions(run_id,person,id);
        CREATE TABLE IF NOT EXISTS synthetic_registry_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation TEXT NOT NULL, run_id INTEGER NOT NULL,
            detail TEXT NOT NULL, created_at TEXT NOT NULL
        );
    ''')
    run_cols = {row['name'] for row in c.execute('PRAGMA table_info(synthetic_registry_runs)')}
    if 'parent_run_id' not in run_cols:
        c.execute('ALTER TABLE synthetic_registry_runs ADD COLUMN parent_run_id INTEGER')
    roster_maintenance.initialize(c)
    # Upgrade old published candidates without replacing their historical
    # raw event evidence. The existing 'appearances' values remain unchanged
    # for legacy records, as they had one event per raw observation.
    candidate_cols = {r['name'] for r in c.execute('PRAGMA table_info(synthetic_registry_candidates)')}
    if 'raw_observations' not in candidate_cols:
        c.execute('ALTER TABLE synthetic_registry_candidates ADD COLUMN raw_observations INTEGER NOT NULL DEFAULT 0')
        for row in c.execute('SELECT id,observation_ids FROM synthetic_registry_candidates'):
            c.execute('UPDATE synthetic_registry_candidates SET raw_observations=? WHERE id=?',
                      (len(json.loads(row['observation_ids'])), row['id']))


class GenerateIn(BaseModel):
    days: int = Field(default=7, ge=1, le=365)
    strategy: Literal['earliest', 'frequency', 'manual'] = 'earliest'
    correction_mode: Literal['rebuild', 'reconcile'] = 'rebuild'


class DecisionIn(BaseModel):
    person: str = Field(pattern=r'^P\d{3}$')
    place_id: int | None = Field(default=None, ge=1)
    action: Literal['assign', 'remove'] = 'assign'
    reason: str = Field(min_length=2, max_length=400)


def _effective_rows(c):
    # Always derive a roster from the current, reviewed synthetic observations.
    # A raw source observation cannot be overwritten by a reviewer.
    return [dict(r) for r in c.execute('''
        SELECT o.id, o.event_key, o.occurrence_key, o.observed_at, o.place_id, o.person_label,
          COALESCE((SELECT r.corrected_person FROM synthetic_observation_reviews r
                    WHERE r.observation_id=o.id ORDER BY r.id DESC LIMIT 1),o.person_label)
                    AS effective_person,
          COALESCE((SELECT r.excluded FROM synthetic_observation_reviews r
                    WHERE r.observation_id=o.id ORDER BY r.id DESC LIMIT 1),0) AS excluded
        FROM synthetic_observations o ORDER BY o.observed_at,o.id
    ''')]


def _data_fingerprint(rows):
    # Backwards-compatible hash: NULL occurrence_key retains the old
    # fingerprint, so pre-migration published snapshots do not falsely stale.
    identity = [[r['id'], r['effective_person'], r['place_id'], r['observed_at']]
                + ([r['occurrence_key']] if r.get('occurrence_key') else [])
                for r in sorted(rows, key=lambda x:x['id'])]
    return sha256(json.dumps(identity, ensure_ascii=False, separators=(',',':')).encode()).hexdigest()


def _entries(c, run_id):
    return [dict(row) for row in c.execute('''
        SELECT person,place_id,first_seen,appearances,raw_observations,visits,observation_ids
          FROM synthetic_registry_candidates WHERE run_id=? ORDER BY person,place_id
    ''',(run_id,))]


def _latest_decisions(c, run_id):
    return {r['person']:dict(r) for r in c.execute('''
        SELECT d.person,d.place_id,d.action,d.reason,d.id
          FROM synthetic_registry_decisions d
          JOIN (SELECT person,MAX(id) latest FROM synthetic_registry_decisions
                WHERE run_id=? GROUP BY person) t
          ON t.latest=d.id
    ''',(run_id,))}


def _selection(c, run):
    by_person = defaultdict(list)
    for row in _entries(c, run['id']):
        row['observation_ids'] = json.loads(row['observation_ids'])
        by_person[row['person']].append(row)
    decisions = _latest_decisions(c, run['id'])
    people = sorted(set(by_person) | set(decisions))
    output = []
    for person in people:
        locs = by_person.get(person, [])
        review = decisions.get(person)
        if review:
            place_id = review['place_id'] if review['action']=='assign' else None
            basis = 'manual' if not review['reason'].startswith('[carry-forward]') else 'carried_manual'
            unresolved = False
        elif len(locs) == 1:
            place_id, basis, unresolved = locs[0]['place_id'], 'single_place', False
        elif run['strategy'] == 'manual':
            place_id, basis, unresolved = None, 'manual_required', True
        elif locs:
            if run['strategy'] == 'frequency':
                pick = min(locs,key=lambda r:(-r['appearances'],r['first_seen'],r['place_id']))
            else:
                pick = min(locs,key=lambda r:(r['first_seen'],r['place_id']))
            place_id, basis, unresolved = pick['place_id'], run['strategy'], False
        else:
            place_id, basis, unresolved = None, 'unknown', True
        output.append({'person':person,'place_id':place_id,'basis':basis,
                       'unresolved':unresolved,'candidates':locs,
                       'manual_reason':review['reason'] if review else None})
    return output


def _run(c, run_id):
    row = c.execute('SELECT * FROM synthetic_registry_runs WHERE id=?',(run_id,)).fetchone()
    if not row:
        raise HTTPException(404,'找不到该合成底库生成批次')
    return dict(row)


def _latest(c):
    row = c.execute('SELECT * FROM synthetic_registry_runs ORDER BY id DESC LIMIT 1').fetchone()
    return dict(row) if row else None


def _published(c):
    row = c.execute("SELECT * FROM synthetic_registry_runs WHERE state='published' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def _window_rows(all_rows, start, end=None):
    # A published historical snapshot has a fixed inclusive end boundary.
    return [r for r in all_rows if not r['excluded'] and r['observed_at']>=start
            and (end is None or r['observed_at']<=end)]


def _is_stale(c, run):
    rows = _window_rows(_effective_rows(c),run['window_start'],run['window_end'])
    return _data_fingerprint(rows)!=run['input_signature']


def present(c, run=None):
    run = run or _latest(c)
    if not run:
        return {'mode':'synthetic_only','ready':False,'run':None,'people':[],
                'conflicts':[],'candidate_count':0,'unresolved_count':0,
                'disclaimer':'仅基于合成观测；不包含真实人员识别或人脸聚类。'}
    selected = _selection(c,run)
    conflicts = [p for p in selected if len(p['candidates'])>1]
    return {'mode':'synthetic_only','ready':True,'run':run,
            'stale':_is_stale(c,run), 'people':selected,
            'conflicts':conflicts,
            'candidate_count':sum(len(p['candidates']) for p in selected),
            'unresolved_count':sum(p['unresolved'] for p in selected),
            'disclaimer':'P001等标识均为虚构；出现次数按显式事件键去重，原始观测数与到访数单独展示。'}


def active_assignments(c):
    """Published synthetic affiliations; stale snapshots are NOT silently trusted."""
    run = _published(c)
    if not run:
        return {}, {'state':'none','revision':None}
    if _is_stale(c,run):
        return {}, {'state':'stale','revision':run['id'],
                    'note':'合成观测已变化，请重新生成并发布底库；规则暂按原有自动归属分析。'}
    selected = _selection(c,run)
    revision = max((r['id'] for r in _latest_decisions(c,run['id']).values()),default=0)
    return {p['person']:p['place_id'] for p in selected}, {
        'state':'published','revision':f"{run['id']}:{revision}", 'assigned_people':len(selected)}


def generate(c, data:GenerateIn):
    observations = [r for r in _effective_rows(c) if not r['excluded']]
    if not observations:
        raise HTTPException(409,'没有有效合成观测；请先生成演示数据。')
    latest = max(r['observed_at'] for r in observations)
    # Fixed range anchored at scenario latest (not runtime wallclock).
    start = (datetime.fromisoformat(latest) - timedelta(days=data.days)).isoformat()
    rows = _window_rows(observations,start)
    if not rows:
        raise HTTPException(409,'所选时间范围没有有效合成观测')
    prev = _published(c)
    cur = c.execute('''INSERT INTO synthetic_registry_runs
        (strategy,correction_mode,days,window_start,window_end,input_signature,state,created_at,parent_run_id)
        VALUES (?,?,?,?,?,?,'draft',?,?)''',
        (data.strategy,data.correction_mode,data.days,start,latest,
         _data_fingerprint(rows),utc_now(),prev['id'] if prev else None))
    rid = cur.lastrowid
    groups=defaultdict(list)
    for r in rows:
        groups[(r['effective_person'],r['place_id'])].append(r)
    for (person,place),arr in sorted(groups.items()):
        arr.sort(key=lambda x:(x['observed_at'],x['id']))
        visit_count = 0
        last = None
        for r in arr:
            dt = datetime.fromisoformat(r['observed_at'])
            if last is None or (dt-last).total_seconds()>20*60:
                visit_count+=1
            last=dt
        c.execute('''INSERT INTO synthetic_registry_candidates
        (run_id,person,place_id,first_seen,appearances,raw_observations,visits,observation_ids)
        VALUES(?,?,?,?,?,?,?,?)''',(rid,person,place,arr[0]['observed_at'],
                              appearance_count(arr),len(arr),
                              visit_count,json.dumps([r['id'] for r in arr])))
    if data.correction_mode=='reconcile' and prev:
        old = _latest_decisions(c,prev['id'])
        valid = {(person,place) for person,place in groups}
        for person,r in old.items():
            if r['action']=='assign' and (person,r['place_id']) in valid:
                c.execute('''INSERT INTO synthetic_registry_decisions
                    (run_id,person,place_id,action,reason,created_at) VALUES(?,?,?,?,?,?)''',
                    (rid,person,r['place_id'],'assign',
                     '[carry-forward] '+r['reason'],utc_now()))
    c.execute('INSERT INTO synthetic_registry_audit(operation,run_id,detail,created_at) VALUES(?,?,?,?)',
              ('generate',rid,json.dumps(data.model_dump(),ensure_ascii=False),utc_now()))
    return present(c,_run(c,rid))



def diff_runs(c, run_id):
    """Compare simulated home assignments to the actual parent published run.

    The parent reference survives publication/archiving. No real person identity
    or Frigate track is ever used in these comparisons.
    """
    run = _run(c,run_id)
    parent = _run(c,run['parent_run_id']) if run.get('parent_run_id') else None
    before = {p['person']:p for p in _selection(c,parent)} if parent else {}
    after = {p['person']:p for p in _selection(c,run)}
    counts = {key:0 for key in ('added','removed','moved','unchanged','unresolved')}
    changes=[]
    for person in sorted(set(before)|set(after)):
        old,new=before.get(person),after.get(person)
        prev_place=old['place_id'] if old else None
        next_place=new['place_id'] if new else None
        if new and new['unresolved']:
            kind='unresolved'
        elif prev_place is None and next_place is not None:
            kind='added'
        elif prev_place is not None and next_place is None:
            kind='removed'
        elif prev_place != next_place:
            kind='moved'
        else:
            kind='unchanged'
        counts[kind]+=1
        if kind != 'unchanged':
            changes.append({'person':person,'kind':kind,
                            'before_place_id':prev_place,'after_place_id':next_place,
                            'basis':new['basis'] if new else None,
                            'before_observation_ids':sorted({
                                evidence for x in old['candidates'] for evidence in x['observation_ids']
                            }) if old else [],
                            'after_observation_ids':sorted({
                                evidence for x in new['candidates'] for evidence in x['observation_ids']
                            }) if new else []})
    return {'mode':'synthetic_only','from_run_id':parent['id'] if parent else None,
            'to_run_id':run_id,'state':run['state'],'stale':_is_stale(c,run),
            'counts':counts,'changes':changes,
            'note':'只比较合成 P### 主归属；原始匿名视频事件不参与人员身份分析。'}


def decide(c, run_id, data:DecisionIn):
    run = _run(c,run_id)
    if run['state']!='draft':
        raise HTTPException(409,'只有草稿底库允许人工裁决；请新建矫正草稿，不可直接修改已发布版本')
    if _is_stale(c,run):
        raise HTTPException(409,'原始模拟观测已修改，请重新生成底库再做人工矫正')
    if data.action=='assign':
        if data.place_id is None:
            raise HTTPException(422,'指定归属场所不能为空')
        place = c.execute('SELECT id,active FROM places WHERE id=?',(data.place_id,)).fetchone()
        if not place or not place['active']:
            raise HTTPException(404,'目标场所不存在或已停用')
    else:
        if data.place_id is not None:
            raise HTTPException(422,'移出底库时无需提供目标场所')
    c.execute('''INSERT INTO synthetic_registry_decisions
        (run_id,person,place_id,action,reason,created_at) VALUES(?,?,?,?,?,?)''',
        (run_id,data.person,data.place_id,data.action,data.reason,utc_now()))
    c.execute('INSERT INTO synthetic_registry_audit(operation,run_id,detail,created_at) VALUES(?,?,?,?)',
              ('manual_'+data.action,run_id,data.model_dump_json(),utc_now()))
    return present(c,run)


def publish(c, run_id):
    run = _run(c,run_id)
    if run['state']!='draft':
        raise HTTPException(409,'仅草稿批次可以发布')
    if _is_stale(c,run):
        raise HTTPException(409,'观测数据发生变化，请重新生成底库')
    selection = _selection(c,run)
    pending=[p['person'] for p in selection if p['unresolved']]
    if pending:
        raise HTTPException(409,{'message':'仍存在需要人工裁决的多场所模拟人员','people':pending})
    c.execute("UPDATE synthetic_registry_runs SET state='archived' WHERE state='published'")
    c.execute("UPDATE synthetic_registry_runs SET state='published',published_at=? WHERE id=?",
              (utc_now(),run_id))
    c.execute('INSERT INTO synthetic_registry_audit(operation,run_id,detail,created_at) VALUES(?,?,?,?)',
              ('publish',run_id,f'assigned={sum(p["place_id"] is not None for p in selection)}',utc_now()))
    return present(c,_run(c,run_id))


def register(app,db):
    @app.get('/base')
    def ui():
        from pathlib import Path
        from fastapi.responses import FileResponse
        return FileResponse(Path(__file__).resolve().parents[1]/'static'/'base.html')

    @app.get('/api/base')
    def current():
        with db() as c: return present(c)

    @app.get('/api/base/active')
    def active():
        with db() as c:
            run=_published(c)
            return present(c,run) if run else {'ready':False,'mode':'synthetic_only','run':None}

    @app.post('/api/base/generate')
    def create(data:GenerateIn):
        with db() as c: return generate(c,data)

    @app.get('/api/base/maintenance/config')
    def maintenance_config():
        with db() as c: return roster_maintenance.configuration(c)

    @app.put('/api/base/maintenance/config')
    def set_maintenance_config(policy:roster_maintenance.Policy):
        with db() as c: return roster_maintenance.configure(c,policy)

    @app.post('/api/base/maintenance/tick')
    def maintenance_tick():
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return roster_maintenance.tick(c,force=True)

    @app.get('/api/base/runs/{run_id}/diff')
    def diff(run_id:int):
        with db() as c: return diff_runs(c,run_id)

    @app.get('/api/base/runs')
    def list_runs():
        with db() as c:
            return [dict(r) for r in c.execute('SELECT * FROM synthetic_registry_runs ORDER BY id DESC LIMIT 30')]

    @app.post('/api/base/runs/{run_id}/decide')
    def review(run_id:int,data:DecisionIn):
        with db() as c: return decide(c,run_id,data)

    @app.post('/api/base/runs/{run_id}/publish')
    def release(run_id:int):
        with db() as c: return publish(c,run_id)

    @app.get('/api/base/audit')
    def audit():
        with db() as c:
            return [dict(r) for r in c.execute('SELECT * FROM synthetic_registry_audit ORDER BY id DESC LIMIT 100')]
