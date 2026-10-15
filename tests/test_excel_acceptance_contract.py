"""Hard stop on claiming Excel delivery without traceable requirement evidence.

This traceability gate does not *replace* functional tests. It ensures every
original Excel row (7..29) is explicitly mapped to existing regression files,
each gap is disclosed, and synthetic identity is never mislabeled 'real'.
"""
import json
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/"config"/"excel_acceptance_v1.json"
EXCEL_ROW_NAMES=[
    "人脸解析",
    "州、市县、街道和社区四级",
    "各场所陌生人底库建立",
    "场所态势",
    "异常人员态势",
    "串所预警",
    "串所同行人预警",
    "串所互动预警",
    "串所聚集预警",
    "久未出现预警",
    "出现预警",
    "场所人数异常预警",
    "场所出入口",
    "人脸检索",
    "人员检索",
    "抓拍结果查询",
    "轨迹回放",
    "关系图谱",
    "人员活动分析",
    "场所管理",
    "场所人员底库管理",
    "预警设置管理",
    "账号管理"
]
STATUSES={'verified_real','synthetic_only','partial','missing'}


def test_exactly_23_excel_requirements_and_evidence_files_exist():
    data=json.loads(MANIFEST.read_text(encoding='utf-8'))
    assert data['source_verified_count']==23
    assert data['source_worksheet']=='需求'
    items=data['requirements']
    assert len(items)==23
    assert [x['id'] for x in items]==list(range(1,24))
    assert [x['excel_row'] for x in items]==list(range(7,30))
    assert [x['title'] for x in items]==EXCEL_ROW_NAMES
    assert all(x['status'] in STATUSES for x in items)
    assert all(x['verified_scope'] and x['remaining_gap'] for x in items)
    for item in items:
        # The evidence file is an existing pytest suite; status != pass claim
        # until that suite actually runs in the CI job.
        assert item['evidence_files'],item['id']
        for path in item['evidence_files']:
            assert path.startswith('tests/') and '..' not in path,path
            assert (ROOT/path).is_file(),(item['id'],path)
    counts=Counter(x['status'] for x in items)
    assert counts=={'verified_real':3,'synthetic_only':10,
                    'partial':8,'missing':2},counts


def test_non_existent_real_biometric_features_are_not_reported_as_complete():
    rows={r['id']:r for r in json.loads(MANIFEST.read_text(
        encoding='utf-8'))['requirements']}
    # Excel item 1 explicitly requires GB28181 realtime+recorded multi-stream
    # face parsing, clustering and comparison. Having one GB SIP transport pass
    # isn't evidence that every part of that feature passed.
    assert rows[1]['status']=='partial'
    # Rows 14 and 17 need cross-camera real-face retrieval and real GPS-style
    # visual traces; neither a P### text search nor image boxes can prove them.
    assert rows[14]['status']==rows[17]['status']=='missing'
    assert all(rows[i]['status']=='synthetic_only' for i in range(6,13))
    # A manual QA tag on an anonymous detector box never validates the
    # person's identity. Keep the actual identity-roster simulation explicit.
    assert rows[3]['status']=='synthetic_only'
    assert rows[15]['status']!='verified_real'
    assert rows[18]['status']!='verified_real'
    assert rows[19]['status']!='verified_real'
    assert rows[21]['status']=='synthetic_only'
    assert rows[22]['status']=='synthetic_only'
    assert rows[2]['status']=='verified_real'
    assert rows[20]['status']=='verified_real'
    assert rows[23]['status']=='verified_real'


def test_public_packaged_source_carries_excel_gap_disclosure():
    text=(ROOT/'scripts'/'package_local_demo.py').read_text(encoding='utf-8')
    assert '"config"' in text
    assert MANIFEST.exists()
    # No acceptance result may claim detector accuracy, verified real identity
    # or true video-derived visits merely because static API routes exist.
    for r in json.loads(MANIFEST.read_text(encoding='utf-8'))['requirements']:
        if r['id'] in {1,3,6,7,8,9,10,11,12,14,15,17,18,19,21,22}:
            assert r['status']!='verified_real',r
