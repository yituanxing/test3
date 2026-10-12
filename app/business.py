"""Pure, deterministic business RULE SIMULATOR over explicitly fabricated identities.

No biometric embeddings, video frames, Frigate labels, or real-person tracks enter
this module. Observations are synthetic classroom/demo fixtures only.

Stages: immutable synthetic observations -> append-only corrections -> visits
-> mutually exclusive synthetic home assignment -> rule hits + evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import combinations

RULE_NAMES = {
    'cross_place': '跨场所出现（合成）',
    'companion': '跨场所同行（合成）',
    'interaction': '双向交叉出现（合成）',
    'gathering': '多归属共同出现（合成）',
    'absence': '长期未观测（合成）',
    'appearance': '指定场所出现（合成）',
    'headcount': '场所人数偏差（合成）',
}
DEFAULT_CONFIG = {
    'visit_gap_minutes': 20,
    'encounter_window_minutes': 10,
    'companion_min_episodes': 2,
    'gathering_min_people': 3,
    'gathering_min_episodes': 1,
    'absence_days': 3,
    'headcount_difference': 2,
    'watch_place_id': 0,
    'affiliation_mode': 'earliest',
}


def _time(s):
    dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('datetime must have a timezone')
    return dt.astimezone(timezone.utc)


def _hit(kind, key_parts, text, subjects, place_ids, evidence, detail):
    # Deterministic key lets reviewers annotate a result while preserving raw facts.
    signature = f"{kind}|{'|'.join(map(str,key_parts))}"
    key = sha256(signature.encode('utf-8')).hexdigest()[:20]
    return {
        'key': key, 'type': kind, 'type_name': RULE_NAMES[kind],
        'title': text, 'people': sorted(set(subjects)),
        'place_ids': sorted(set(place_ids)), 'observation_ids': sorted(set(evidence)),
        'detail': detail, 'origin': 'synthetic_only',
        'status': '待复核',
    }


def analyze(observations: list[dict], config: dict, *, now: datetime | None = None) -> dict:
    """Pure re-computation from inputs. No caches or database side effects."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    raw = []
    for r in observations:
        if r.get('excluded'):
            continue
        person = r.get('effective_person') or r['person_label']
        raw.append({'id': r['id'], 'person': person, 'place_id': r['place_id'],
                    'time': _time(r['observed_at'])})
    raw.sort(key=lambda v: (v['time'], v['id']))

    # Sessionize by exact synthetic person + place, merging only near-consecutive
    # observations. Keep all supporting immutable observation ids.
    sessions = defaultdict(list)
    for r in raw:
        sessions[(r['person'], r['place_id'])].append(r)
    gap = timedelta(minutes=config['visit_gap_minutes'])
    visits = []
    for (person, place), entries in sessions.items():
        last = None
        for entry in entries:
            if last is None or entry['time'] - last['last'] > gap:
                last = {'person': person, 'place_id': place, 'start': entry['time'],
                        'last': entry['time'], 'observation_ids': [entry['id']]}
                visits.append(last)
            else:
                last['last'] = entry['time']
                last['observation_ids'].append(entry['id'])
    visits.sort(key=lambda v: (v['start'], v['person'], v['place_id']))

    # Each synthetic person belongs to one simulated base location. Stable
    # tie-breaking, with full historical observations retained in other places.
    groups = defaultdict(list)
    for v in visits:
        groups[v['person']].append(v)
    affiliation = {}
    for person, arr in groups.items():
        by_place = defaultdict(list)
        for v in arr:
            by_place[v['place_id']].append(v)
        if config['affiliation_mode'] == 'frequency':
            affiliation[person] = min(by_place, key=lambda p:
                (-len(by_place[p]), min(v['start'] for v in by_place[p]), p))
        else:
            affiliation[person] = min(by_place, key=lambda p:
                (min(v['start'] for v in by_place[p]), p))

    # Co-presence episodes are SIMULATED approximate time windows, not proof
    # that two actual humans physically met or interacted.
    time_window = timedelta(minutes=config['encounter_window_minutes'])
    by_place = defaultdict(list)
    for v in visits:
        by_place[v['place_id']].append(v)
    episodes = []
    for place, arr in by_place.items():
        current = []
        anchor = None
        for v in arr:
            if anchor is None or v['start'] - anchor > time_window:
                if current:
                    episodes.append({'place_id': place, 'start': anchor, 'visits': current})
                current, anchor = [], v['start']
            current.append(v)
        if current:
            episodes.append({'place_id': place, 'start': anchor, 'visits': current})
    episodes.sort(key=lambda e: (e['start'], e['place_id']))
    alerts = []

    for v in visits:
        if v['place_id'] != affiliation[v['person']]:
            alerts.append(_hit('cross_place', [v['person'], v['place_id'], v['start'].isoformat()],
                f"{v['person']} 出现在非主归属场所", [v['person']],
                [affiliation[v['person']], v['place_id']], v['observation_ids'],
                '这是按合成主归属规则得到的模拟命中，不能单凭跨场所出现推断风险。'))

    # Count distinct episodes shared by people with the same synthetic home,
    # in a common *other* place; a single sustained visit counts once.
    shared = defaultdict(list)
    for ep in episodes:
        by_person = {v['person']: v for v in ep['visits']}
        for a, b in combinations(sorted(by_person), 2):
            if affiliation[a] == affiliation[b] and affiliation[a] != ep['place_id']:
                shared[(a, b)].append(ep)
    for (a, b), eps in shared.items():
        if len(eps) >= config['companion_min_episodes']:
            ids = [i for ep in eps for v in ep['visits'] if v['person'] in (a, b)
                   for i in v['observation_ids']]
            alerts.append(_hit('companion', [a, b],
                f"{a} 与 {b} 在其他场所共同出现 {len(eps)} 个时间窗口",
                [a, b], [ep['place_id'] for ep in eps], ids,
                '窗口共同出现仅为时间关联，不能确定实际同行关系。'))

    # Directional encounters in BOTH participants' simulated home places.
    interaction = defaultdict(lambda: defaultdict(list))
    for ep in episodes:
        by_person = {v['person']: v for v in ep['visits']}
        for a, b in combinations(sorted(by_person), 2):
            if affiliation[a] != affiliation[b] and ep['place_id'] in (affiliation[a], affiliation[b]):
                interaction[(a, b)][ep['place_id']].append(ep)
    for (a, b), loc in interaction.items():
        if loc.get(affiliation[a]) and loc.get(affiliation[b]):
            eps = loc[affiliation[a]] + loc[affiliation[b]]
            ids = [i for ep in eps for v in ep['visits'] if v['person'] in (a, b)
                   for i in v['observation_ids']]
            alerts.append(_hit('interaction', [a, b],
                f"{a} 与 {b} 在双方主场所均有共同出现窗口", [a, b],
                list(loc), ids, '这是人工虚构的双向出现示例，不证明实际交往。'))

    gathering = defaultdict(list)
    for ep in episodes:
        by_person = {v['person']: v for v in ep['visits']}
        homes = {affiliation[p] for p in by_person}
        if len(by_person) >= config['gathering_min_people'] and len(homes) >= 2:
            gathering[ep['place_id']].append((ep, by_person))
    for place, group in gathering.items():
        if len(group) >= config['gathering_min_episodes']:
            people = sorted({p for _, persons in group for p in persons})
            evidence = [i for ep,_ in group for v in ep['visits'] for i in v['observation_ids']]
            alerts.append(_hit('gathering', [place],
                f"场所 {place} 存在 {len(group)} 个跨归属多人共同出现窗口",
                people, [place], evidence,
                '只是合成时间窗口的人数统计；不代表真实组织或结队。'))

    for person, arr in groups.items():
        last = max(arr, key=lambda v: v['last'])
        days = (now-last['last']).total_seconds() / 86400
        if days >= config['absence_days']:
            alerts.append(_hit('absence', [person],
                f"{person} 已 {days:.1f} 天没有模拟观测", [person],
                [affiliation[person]], last['observation_ids'],
                '无观测不代表实际未到访；设备离线和漏检均可导致缺失。'))

    watch = config['watch_place_id']
    if watch:
        for v in visits:
            if v['place_id'] == watch:
                alerts.append(_hit('appearance', [watch, v['person'], v['start'].isoformat()],
                    f"{v['person']} 出现在演示指定场所 {watch}", [v['person']],
                    [watch], v['observation_ids'], '仅用于虚构样本的可配置位置事件示例。'))

    # Count deduplicated synthetic visitors per place per UTC day, vs count
    # of synthetic home assignments. Do not count raw detections as people.
    residents = Counter(affiliation.values())
    calendar = defaultdict(set)
    calendar_ids = defaultdict(list)
    for v in visits:
        if v['start'].date() == now.date():
            calendar[v['place_id']].add(v['person'])
            calendar_ids[v['place_id']].extend(v['observation_ids'])
    for place in sorted(set(residents) | set(calendar)):
        actual = len(calendar[place])
        baseline = residents[place]
        if abs(actual-baseline) >= config['headcount_difference']:
            alerts.append(_hit('headcount', [place, now.date()],
                f"场所 {place} 当日去重人数 {actual}，底库 {baseline}，差值 {actual-baseline:+d}",
                calendar[place], [place], calendar_ids[place],
                '仅为合成事件估算，不能用于实际人数异常处置。'))

    response_visits = [{**{k:v[k] for k in ('person','place_id','observation_ids')},
                        'started_at':v['start'].isoformat(), 'ended_at':v['last'].isoformat(),
                        'raw_observations':len(v['observation_ids'])} for v in visits]
    counts = Counter(a['type'] for a in alerts)
    return {
        'raw_count': len(observations), 'effective_observations':len(raw),
        'visit_count':len(visits), 'person_count':len(affiliation),
        'affiliation': [{'person':p, 'place_id':home, 'visit_count':len(groups[p])}
                        for p,home in sorted(affiliation.items())],
        'visits':response_visits,
        'rule_counts':[{ 'type':t,'name':label,'count':counts[t]} for t,label in RULE_NAMES.items()],
        'alerts':sorted(alerts, key=lambda a:(a['type'], a['key'])),
        'mode':'synthetic_only',
        'disclaimer':'合成身份、合成轨迹的规则模拟器；与真实视频/Frigate人员检测严格隔离。',
    }
