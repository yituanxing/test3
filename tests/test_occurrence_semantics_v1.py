"""P0 requirements #1/#3: raw frame observations != occurrence events != visits.

All P### labels are synthetic ground-truth fixtures, never inferred from real
Frigate track IDs or from biometric images.
"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from app import base_registry
from app.business import analyze, DEFAULT_CONFIG
from app.occurrence_semantics import appearance_count


START = datetime(2026,10,12,8,tzinfo=timezone.utc)


def _obs(index, person, place, minute, occurrence):
    return {'id': index, 'event_key': f'SYN-{index}',
            'occurrence_key': occurrence, 'person_label': person,
            'place_id': place, 'observed_at': (START + timedelta(minutes=minute)).isoformat()}


def test_100_frames_one_appearance_three_other_events_win_frequency():
    a = [_obs(i, 'P001', 1, i / 100, 'CAM-A:TRACK-001') for i in range(1,101)]
    b = [_obs(100+i, 'P001', 2, 60*i, f'CAM-B:EVENT-{i}') for i in range(1,4)]
    assert appearance_count(a) == 1
    assert appearance_count(b) == 3
    assert appearance_count(a+b) == 4
    cfg={**DEFAULT_CONFIG, 'affiliation_mode':'frequency', 'absence_days':365,
         'headcount_difference':1000}
    result=analyze(a+b,cfg,now=START+timedelta(hours=10))
    assert result['raw_count']==103
    assert result['affiliation'][0]['place_id']==2


def test_legacy_rows_independent_and_place_scoped():
    old = [_obs(i,'P001',1,i,None) for i in range(1,4)]
    assert appearance_count(old) == 3
    # Same occurrence strings at different places and for other simulated
    # persons must not combine into one occurrence event.
    crossed=[_obs(100,'P001',1,2,'T1'),_obs(101,'P001',2,2,'T1'),
             _obs(102,'P002',1,2,'T1')]
    assert appearance_count(crossed)==3


def test_sqlite_candidate_keeps_raw_and_deduped_counts_and_published_version(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'DB',tmp_path/'occurrence.sqlite3')
    main.setup_db()
    with main.db() as c:
        places = [x['id'] for x in c.execute('SELECT id FROM places ORDER BY id LIMIT 2')]
        a,b=places
        rows=[_obs(i,'P001',a,i/100,'A-TRACK-1') for i in range(1,101)]
        rows += [_obs(100+i,'P001',b,60*i,f'B-TRACK-{i}') for i in range(1,4)]
        c.executemany('''INSERT INTO synthetic_observations
            (event_key,occurrence_key,person_label,place_id,observed_at,origin)
            VALUES (?,?,?,?,?,'synthetic_only')''',
            [(v['event_key'],v['occurrence_key'],v['person_label'],v['place_id'],v['observed_at']) for v in rows])
        r=base_registry.generate(c,base_registry.GenerateIn(strategy='frequency'))
        person=next(p for p in r['people'] if p['person']=='P001')
        assert person['place_id']==b
        cand={p['place_id']:p for p in person['candidates']}
        assert cand[a]['appearances']==1 and cand[a]['raw_observations']==100
        assert cand[b]['appearances']==3 and cand[b]['raw_observations']==3
        assert cand[a]['visits']==1 and cand[b]['visits']==3
        base_registry.publish(c,r['run']['id'])
        assert base_registry.active_assignments(c)[0]['P001']==b
        # Edit one explicit event group: stale check must notice the change.
        c.execute("UPDATE synthetic_observations SET occurrence_key='A-TRACK-2' WHERE event_key='SYN-1'")
        assert base_registry._is_stale(c,base_registry._published(c))


def test_legacy_migration_preserves_published_signature(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'DB',tmp_path/'old.sqlite3')
    main.setup_db()
    with main.db() as c:
        id1=c.execute('SELECT id FROM places ORDER BY id LIMIT 1').fetchone()['id']
        c.execute("""INSERT INTO synthetic_observations(event_key,person_label,place_id,observed_at,origin)
                  VALUES(?,?,?,?, 'synthetic_only')""",('LEGACY-1','P001',id1,START.isoformat()))
        candidate=base_registry.generate(c,base_registry.GenerateIn())
        base_registry.publish(c,candidate['run']['id'])
        before=base_registry._published(c)['input_signature']
    # Repeated init mimics an existing user's DB on next boot
    main.setup_db()
    with main.db() as c:
        assert before == base_registry._published(c)['input_signature']
        assert base_registry.active_assignments(c)[1]['state']=='published'
        person=base_registry.present(c,base_registry._published(c))['people'][0]
        assert person['candidates'][0]['appearances']==1
        assert person['candidates'][0]['raw_observations']==1
