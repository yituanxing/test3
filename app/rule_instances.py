"""Shared synthetic-only rule instance CRUD and evaluation.

All rule instances re-use app.business.analyze; they never access cameras,
faces, animations, media or real-world identities. This is Excel #22 DEMO.
An active or disabled non-archived instance of a given kind takes ownership of
that kind, replacing legacy singleton hits without duplicating their counts.
Other legacy kinds stay 100% compatible.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app import access
from app.business import RULE_NAMES, analyze

router = APIRouter()
_db = None
RuleKind = Literal['cross_place','companion','interaction','gathering',
                   'absence','appearance','headcount']

# Reuse old validated business semantics instead of creating seven engines.
PARAM_BOUNDS = {
    'cross_place': {},
    'companion': {'companion_min_episodes':(1,20),
                  'companion_min_people':(2,20),
                  'encounter_window_minutes':(1,120)},
    'interaction': {'interaction_min_rounds':(1,20),
                    'encounter_window_minutes':(1,120)},
    'gathering': {'gathering_min_people':(2,100),
                  'gathering_min_episodes':(1,20),
                  'encounter_window_minutes':(1,120)},
    'absence': {'absence_days':(1,365),'absence_check_interval_days':(1,365),
                'absence_missed_checks':(1,365)},
    'appearance': {},
    'headcount': {'headcount_difference':(1,1000)},
}


class RuleInstanceIn(BaseModel):
    type: RuleKind
    name: str = Field(min_length=2, max_length=80)
    enabled: bool = True
    parameters: dict[str, int] = Field(default_factory=dict, max_length=8)
    target_place_ids: list[int] = Field(default_factory=list, max_length=30)
    target_synthetic_people: list[str] = Field(default_factory=list, max_length=50)


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def initialize(c):
    c.executescript("""CREATE TABLE IF NOT EXISTS synthetic_rule_instances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
        parameters_json TEXT NOT NULL DEFAULT '{}',
        place_ids_json TEXT NOT NULL DEFAULT '[]',
        people_json TEXT NOT NULL DEFAULT '[]',
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS synthetic_rule_instance_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instance_id INTEGER NOT NULL,
        operation TEXT NOT NULL,
        detail_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS rule_instance_audit_idx
        ON synthetic_rule_instance_audit(instance_id,id);""")


def _row(row):
    result = dict(row)
    result['enabled'] = bool(result['enabled'])
    result['archived'] = bool(result['archived'])
    result['parameters'] = json.loads(result.pop('parameters_json'))
    result['target_place_ids'] = json.loads(result.pop('place_ids_json'))
    result['target_synthetic_people'] = json.loads(result.pop('people_json'))
    result['mode'] = 'synthetic_only'
    return result


def list_instances(c, *, include_archived=False):
    predicate = '' if include_archived else ' WHERE archived=0'
    return [_row(x) for x in c.execute('SELECT * FROM synthetic_rule_instances'
                                       + predicate + ' ORDER BY id')]


def _current(c, instance_id):
    row = c.execute('SELECT * FROM synthetic_rule_instances WHERE id=?',
                    (instance_id,)).fetchone()
    if not row:
        raise HTTPException(404,'规则实例不存在')
    return _row(row)


def _admin(request):
    actor = access.principal(request)
    if actor is not None and actor['level'] != 1:
        raise HTTPException(403,'仅州级管理员能管理模拟预警规则')


def _validate(c, data):
    name = data.name.strip()
    if len(name)<2:
        raise HTTPException(422,'规则名称至少2个字')
    accepted = PARAM_BOUNDS[data.type]
    for key,value in data.parameters.items():
        if key not in accepted:
            raise HTTPException(422,f'{data.type} 不支持参数 {key}')
        lo,hi=accepted[key]
        if not isinstance(value,int) or isinstance(value,bool) or not lo<=value<=hi:
            raise HTTPException(422,f'{key} 必须在 {lo}–{hi} 之间')
    ids = data.target_place_ids
    if len(ids)!=len(set(ids)) or any(x<1 for x in ids):
        raise HTTPException(422,'目标场所ID不得重复，且必须是正整数')
    if ids:
        matches={x['id'] for x in c.execute(
            'SELECT id FROM places WHERE id IN ('+','.join('?' for _ in ids)+')', ids)}
        if matches!=set(ids):
            raise HTTPException(404,'至少一个目标场所不存在')
    persons=data.target_synthetic_people
    import re
    if len(persons)!=len(set(persons)) or any(
            not re.fullmatch(r'P[0-9]{3}',person) for person in persons):
        raise HTTPException(422,'只能填写不重复的 P### 虚构人员编号')
    if data.type == 'appearance' and not ids:
        raise HTTPException(422,'出现预警实例必须指定至少一个模拟场所')
    return name


def _audit(c,instance_id,operation,detail):
    c.execute("""INSERT INTO synthetic_rule_instance_audit
      (instance_id,operation,detail_json,created_at) VALUES(?,?,?,?)""",
      (instance_id,operation,json.dumps(detail,ensure_ascii=False,sort_keys=True),_utc()))


def register(app, db):
    global _db
    _db = db
    app.include_router(router)


@router.get('/api/business/rule-instances')
def instance_list(request:Request, include_archived: bool=False):
    _admin(request)
    with _db() as c:
        return {'mode':'synthetic_only',
                'instances':list_instances(c,include_archived=include_archived)}


@router.get('/api/business/rule-instances/audit')
def instance_audit(request:Request):
    _admin(request)
    with _db() as c:
        return [dict(x) for x in c.execute(
            'SELECT * FROM synthetic_rule_instance_audit ORDER BY id DESC LIMIT 150')]


@router.post('/api/business/rule-instances',status_code=201)
def instance_create(data:RuleInstanceIn,request:Request):
    _admin(request)
    with _db() as c:
        name=_validate(c,data)
        now=_utc()
        row=c.execute("""INSERT INTO synthetic_rule_instances
            (type,name,enabled,parameters_json,place_ids_json,people_json,
             created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (data.type,name,int(data.enabled),json.dumps(data.parameters),
             json.dumps(data.target_place_ids),json.dumps(data.target_synthetic_people),
             now,now))
        result=_current(c,row.lastrowid)
        _audit(c,row.lastrowid,'create',result)
        return result


@router.put('/api/business/rule-instances/{instance_id}')
def instance_update(instance_id:int,data:RuleInstanceIn,request:Request):
    _admin(request)
    with _db() as c:
        before=_current(c,instance_id)
        if before['archived']:
            raise HTTPException(409,'已归档规则不允许重新编辑')
        name=_validate(c,data)
        c.execute("""UPDATE synthetic_rule_instances
            SET type=?,name=?,enabled=?,parameters_json=?,place_ids_json=?,
                people_json=?,version=version+1,updated_at=? WHERE id=?""",
            (data.type,name,int(data.enabled),json.dumps(data.parameters),
             json.dumps(data.target_place_ids),json.dumps(data.target_synthetic_people),
             _utc(),instance_id))
        result=_current(c,instance_id)
        _audit(c,instance_id,'update',{'before':before,'after':result})
        return result


@router.delete('/api/business/rule-instances/{instance_id}')
def instance_archive(instance_id:int,request:Request):
    _admin(request)
    with _db() as c:
        before=_current(c,instance_id)
        if before['archived']:
            raise HTTPException(409,'规则实例已归档')
        c.execute("""UPDATE synthetic_rule_instances SET archived=1,
            enabled=0,version=version+1,updated_at=? WHERE id=?""",
            (_utc(),instance_id))
        _audit(c,instance_id,'archive',{'before':before,'soft_delete':True})
        return {'id':instance_id,'archived':True,'history_retained':True}


def apply_instances(c, base, config, observations, now, affiliation_overrides):
    """Replace each managed kind; evaluate configured instances via ONE engine.

    Every independent instance gets a unique key and revision for review.
    Presence of DISABLED instances still suppresses a legacy default for that
    kind, so a paused instance cannot silently re-enable legacy matching.
    """
    rules=list_instances(c)
    if not rules:
        return base
    managed={x['type'] for x in rules}
    hits=[hit for hit in base['alerts'] if hit['type'] not in managed]
    for inst in rules:
        if not inst['enabled']:
            continue
        rule_config={**config,**inst['parameters'],'enabled_rules':[inst['type']]}
        if inst['type']=='appearance':
            rule_config['watch_place_id']=0
            rule_config['watch_place_ids']=inst['target_place_ids']
            rule_config['watch_person_ids']=inst['target_synthetic_people']
        candidates=analyze(observations,rule_config,now=now,
                           affiliation_overrides=affiliation_overrides)['alerts']
        for hit in candidates:
            if inst['target_place_ids'] and not (
                    set(hit['place_ids']) & set(inst['target_place_ids'])):
                continue
            if inst['target_synthetic_people'] and not (
                    set(hit['people']) & set(inst['target_synthetic_people'])):
                continue
            hit['key']=sha256(
                f"synthetic-instance:{inst['id']}:{hit['key']}".encode()
                ).hexdigest()[:20]
            hit['rule_instance_id']=inst['id']
            hit['rule_instance_name']=inst['name']
            hit['rule_instance_version']=inst['version']
            hit['rule_instance_parameters']=inst['parameters']
            hits.append(hit)
    base['alerts']=sorted(hits,key=lambda hit:(hit['type'],
                     hit.get('rule_instance_id') or 0,hit['key']))
    counts=Counter(hit['type'] for hit in hits)
    base['rule_counts']=[{'type':type,'name':name,'count':counts[type]}
                         for type,name in RULE_NAMES.items()]
    base['active_rule_instances']=[{'id':x['id'],'type':x['type'],
                                  'enabled':x['enabled']} for x in rules]
    return base
