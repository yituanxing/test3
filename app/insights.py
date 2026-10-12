"""Synthetic-only situational dashboards, search and explanatory co-presence graph.

Never correlate real Frigate object events to simulated people. Frigate object
counts are displayed in an independent block and NEVER treated as identities.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any

from fastapi import HTTPException

DISCLAIMER = (
    '所有 P### 人员标识、活动时间线和关系均为合成演示数据。'
    '共同出现在同一模拟时间窗口仅说明数据关联，不证明真实互动、同行或身份。'
)


def stamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('时间戳缺少时区')
    return dt.astimezone(timezone.utc)


def _window(analysis: dict, days: int):
    if not 1 <= days <= 365:
        raise ValueError('days out of range')
    moment = stamp(analysis['simulation_time_utc'])
    last = moment.date()
    first = last - timedelta(days=days - 1)
    return first, last


def _filtered(analysis: dict, *, days: int, place_ids: set[int]) -> dict:
    first, last = _window(analysis, days)
    visits = [v for v in analysis['visits'] if v['place_id'] in place_ids
              and first <= stamp(v['started_at']).date() <= last]
    alerts = [a for a in analysis['alerts'] if place_ids.intersection(a['place_ids'])]
    return {'first': first, 'last': last, 'visits': visits, 'alerts': alerts}


def overview(analysis: dict, places: list[dict], *, days: int = 7) -> dict:
    scope = {p['id'] for p in places}
    data = _filtered(analysis, days=days, place_ids=scope)
    visits, alerts = data['visits'], data['alerts']
    by_id = {p['id']: p['name'] for p in places}
    series = {str(data['first'] + timedelta(days=i)):
              {'day':str(data['first'] + timedelta(days=i)), 'visits':0, 'people':set()}
              for i in range(days)}
    grouped = {p['id']: {'place_id':p['id'], 'place_name':p['name'],
                         'visits':0, 'people':set(), 'alerts':0} for p in places}
    for v in visits:
        day = stamp(v['started_at']).date().isoformat()
        if day in series:
            series[day]['visits'] += 1
            series[day]['people'].add(v['person'])
        grouped[v['place_id']]['visits'] += 1
        grouped[v['place_id']]['people'].add(v['person'])
    for a in alerts:
        for pid in a['place_ids']:
            if pid in grouped:
                grouped[pid]['alerts'] += 1
    people = {v['person'] for v in visits}
    trend = [{'day':v['day'], 'visits':v['visits'], 'unique_synthetic_people':len(v['people'])}
             for v in series.values()]
    ranking = [{**v, 'unique_synthetic_people':len(v['people'])}
               for v in grouped.values()]
    for v in ranking:
        del v['people']
    ranking.sort(key=lambda x:(-x['visits'], x['place_id']))
    recent = [{**a, 'place_names':[by_id.get(pid, f'场所{pid}') for pid in a['place_ids']]}
              for a in alerts[:30]]
    return {
        'mode':'synthetic_only', 'disclaimer':DISCLAIMER,
        'period':{'days':days, 'start_day_utc':str(data['first']), 'end_day_utc':str(data['last']),
                  'scenario_time_utc':analysis['simulation_time_utc']},
        'cards':{'place_count':len(places), 'synthetic_visits':len(visits),
                 'unique_synthetic_people_in_window':len(people), 'rule_hits':len(alerts),
                 'published_roster_state':analysis['registry_status']['state']},
        'trend':trend, 'ranking':ranking, 'recent_alerts':recent,
        'rule_counts':[{**r, 'count':sum(a['type']==r['type'] for a in alerts)} for r in analysis['rule_counts']],
        'notes':{'visits':'按合成观测聚合的到访会话；不是人脸识别结果',
                 'unique_synthetic_people_in_window':'仅指生成的 P### 虚构标识去重',
                 'rule_hits':'对模拟规则结果进行场所交集过滤；不用于真实风险认定'},
    }


def relationships(analysis: dict, *, place_ids: set[int], days: int=7,
                  person: str | None = None) -> dict:
    """Count distinct anchored co-presence time windows, not interactions."""
    data = _filtered(analysis, days=days, place_ids=place_ids)
    grouped: dict[int, list[dict]] = defaultdict(list)
    for v in data['visits']:
        grouped[v['place_id']].append(v)
    window_minutes = int(analysis['config']['encounter_window_minutes'])
    delta = timedelta(minutes=window_minutes)
    edges: dict[tuple[str,str],dict] = {}
    for place_id, arr in grouped.items():
        arr.sort(key=lambda v:(v['started_at'],v['person']))
        windows: list[list[dict]] = []
        anchor = None
        for v in arr:
            current = stamp(v['started_at'])
            if anchor is None or current - anchor > delta:
                windows.append([])
                anchor = current
            windows[-1].append(v)
        for window in windows:
            participants = sorted({v['person'] for v in window})
            for a,b in combinations(participants,2):
                key = (a,b)
                edge = edges.setdefault(key, {'person_a':a,'person_b':b,'windows':0,
                                              'place_ids':set(), 'evidence_visits':0})
                edge['windows'] += 1
                edge['place_ids'].add(place_id)
                edge['evidence_visits'] += sum(v['person'] in (a,b) for v in window)
    out = [{**e, 'place_ids':sorted(e['place_ids'])} for e in edges.values()
           if not person or person in (e['person_a'], e['person_b'])]
    out.sort(key=lambda x:(-x['windows'],x['person_a'],x['person_b']))
    return {'mode':'synthetic_only','window_minutes':window_minutes,'edges':out,
            'disclaimer':'仅为虚构场景的共同出现时间窗口；不是人物关系、交往事实或真实身份识别。'}


def people_search(analysis: dict, places: list[dict], *, days: int = 7,
                  query: str = '', place_id: int | None = None) -> dict:
    allowed = {p['id'] for p in places}
    if place_id is not None:
        if place_id not in allowed:
            raise HTTPException(404,'场所不在当前查询范围')
        allowed = {place_id}
    result = _filtered(analysis, days=days, place_ids=allowed)
    counters: dict[str,dict] = {}
    homes = {r['person']:r['place_id'] for r in analysis['affiliation']}
    names = {p['id']:p['name'] for p in places}
    for v in result['visits']:
        person = v['person']
        if query.strip() and query.strip().upper() not in person.upper():
            continue
        entry = counters.setdefault(person, {'person':person,'synthetic_visits':0,
                    'seen_places':set(), 'last_seen_utc':v['ended_at'],
                    'affiliation_place_id':homes.get(person)})
        entry['synthetic_visits'] += 1
        entry['seen_places'].add(v['place_id'])
        if stamp(v['ended_at']) > stamp(entry['last_seen_utc']):
            entry['last_seen_utc']=v['ended_at']
    people = []
    for entry in counters.values():
        entry['seen_place_ids'] = sorted(entry.pop('seen_places'))
        entry['seen_place_names'] = [names.get(p,f'场所{p}') for p in entry['seen_place_ids']]
        entry['affiliation_place_name'] = names.get(entry['affiliation_place_id'])
        people.append(entry)
    people.sort(key=lambda p:(-p['synthetic_visits'],p['person']))
    return {'mode':'synthetic_only','period_days':days,'count':len(people),
            'people':people[:200], 'truncated':len(people)>200,'disclaimer':DISCLAIMER}


def person_detail(analysis: dict, places: list[dict], person: str, *, days:int=30) -> dict:
    allowed = {p['id'] for p in places}
    data = _filtered(analysis, days=days, place_ids=allowed)
    visits = [v for v in data['visits'] if v['person']==person]
    if not visits:
        raise HTTPException(404,'此查询范围或时间段内没有该虚构人员的到访记录')
    names = {p['id']:p['name'] for p in places}
    visits = [{**v, 'place_name':names.get(v['place_id'],f"场所{v['place_id']}")}
              for v in sorted(visits,key=lambda v:v['started_at'],reverse=True)]
    alerts = [a for a in data['alerts'] if person in a['people']]
    homes = {r['person']:r['place_id'] for r in analysis['affiliation']}
    edges=relationships(analysis,place_ids=allowed,days=days,person=person)['edges']
    return {'mode':'synthetic_only','person':person,'affiliation_place_id':homes.get(person),
            'affiliation_place_name':names.get(homes.get(person)),
            'total_visits':len(visits),'seen_places':len({v['place_id'] for v in visits}),
            'visits':visits,'alerts':alerts[:100],'co_presence':edges[:100],
            'disclaimer':DISCLAIMER}
