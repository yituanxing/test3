"""Production-default authentication + scoped authorization: deny-by-default tests."""
from fastapi.testclient import TestClient
import pytest
from app import access
import app.main as main


@pytest.fixture
def case(monkeypatch,tmp_path):
    monkeypatch.delenv('ZD_AUTH_TEST_COMPAT',raising=False)
    monkeypatch.setattr(main,'DB',tmp_path/'secure.sqlite3')
    main.setup_db()
    with main.db() as c:
        root=c.execute("SELECT id FROM regions WHERE name='演示州'").fetchone()['id']
        county=c.execute("SELECT id FROM regions WHERE name='演示县市'").fetchone()['id']
        street=c.execute("SELECT id FROM regions WHERE name='演示街道'").fetchone()['id']
        leaf=c.execute("SELECT id FROM regions WHERE name='演示社区'").fetchone()['id']
        # Create a distinct county with a fourth-level branch outside demo county
        other_county=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,2)',('县市乙',root)).lastrowid
        other_street=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,3)',('街道乙',other_county)).lastrowid
        other_leaf=c.execute('INSERT INTO regions(name,parent_id,level) VALUES(?,?,4)',('社区乙',other_street)).lastrowid
        other_place=c.execute('INSERT INTO places(name,region_id) VALUES(?,?)',('其他辖区场所',other_leaf)).lastrowid
        local_place=c.execute("SELECT id FROM places WHERE name='测试场所 A'").fetchone()['id']
        for username,region in [('stateuser',root),('countyuser',county),('streetuser',street),('communityuser',leaf)]:
            access.create_user(c,username,'strong-test-password-2026',region)
    client=TestClient(main.app)
    def login(username):
        response=client.post('/api/auth/login',json={'username':username,'password':'strong-test-password-2026'})
        assert response.status_code==200,response.text
        return {'Authorization':'Bearer '+response.json()['access_token']}
    return client,login,locals()


def test_unauthenticated_cannot_enumerate_regions_or_write(case):
    client,_,_=case
    assert client.get('/api/health').status_code==200
    assert client.get('/api/places').status_code==401
    assert client.get('/api/regions').status_code==401
    assert client.post('/api/places',json={'name':'非法','region_id':4}).status_code==401
    assert client.get('/api/business/analysis').status_code==401
    assert client.get('/api/auth/me').status_code==401


def test_invalid_password_does_not_issue_access(case):
    client,login,_=case
    for username,pw in [('stateuser','wrong'),('unknown','strong-test-password-2026')]:
        assert client.post('/api/auth/login',json={'username':username,'password':pw}).status_code==401
    assert client.get('/api/auth/me',headers={'Authorization':'Bearer fake'}).status_code==401
    assert client.get('/api/auth/me',headers=login('streetuser')).json()['role']=='street'


@pytest.mark.parametrize('who',['countyuser','streetuser','communityuser'])
def test_territorial_reads_and_query_override_denied(case,who):
    client,login,v=case
    headers=login(who)
    got=client.get('/api/places',headers=headers)
    assert got.status_code==200,got.text
    ids={p['id'] for p in got.json()}
    assert v['local_place'] in ids and v['other_place'] not in ids
    assert client.get('/api/places?region_id='+str(v['other_leaf']),headers=headers).status_code==403
    regions=client.get('/api/regions',headers=headers).json()
    assert v['other_county'] not in {r['id'] for r in regions}
    assert client.get('/api/monitoring/overview?region_id='+str(v['other_leaf']),headers=headers).status_code==403
    assert client.get('/api/frigate/events?region_id='+str(v['other_leaf']),headers=headers).status_code==403
    assert client.get('/api/insights/people?region_id='+str(v['other_leaf']),headers=headers).status_code==403
    summary=client.get('/api/frigate/summary',headers=headers)
    assert summary.status_code==200
    assert v['other_place'] not in {p['place_id'] for p in summary.json()['places']}


def test_lower_roles_are_readonly_and_restricted_apis_fail_closed(case):
    client,login,v=case
    for name in ('streetuser','communityuser'):
        h=login(name)
        assert client.post('/api/places',headers=h,json={'name':'不允许','region_id':v['leaf']}).status_code==403
        assert client.post('/api/base/generate',headers=h,json={'days':7}).status_code==403
        assert client.get('/api/business/analysis',headers=h).status_code==403
        assert client.get('/api/admin/audit',headers=h).status_code==403
        assert client.get('/api/frigate/quality',headers=h).status_code==403
        assert client.post('/api/regions',headers=h,json={'name':'不允许','parent_id':v['street']}).status_code==403


def test_county_can_manage_local_but_not_move_others(case):
    client,login,v=case
    h=login('countyuser')
    assert client.post('/api/places',headers=h,json={'name':'新增本辖区','region_id':v['leaf']}).status_code==200
    assert client.post('/api/places',headers=h,json={'name':'跨区新建','region_id':v['other_leaf']}).status_code==403
    assert client.put('/api/places/'+str(v['other_place']),headers=h,json={
        'name':'非法修改','region_id':v['leaf'],'active':True}).status_code==403
    assert client.put('/api/places/'+str(v['local_place']),headers=h,json={
        'name':'非法迁移','region_id':v['other_leaf'],'active':True}).status_code==403
    # Cannot turn a local source into cross-region source
    src=client.post('/api/sources',headers=h,json={
        'name':'本辖区视频','kind':'file','place_id':v['local_place'],'uri':'/test.mp4'})
    assert src.status_code==200,src.text
    sid=src.json()['id']
    assert client.put('/api/sources/'+str(sid),headers=h,json={
        'name':'错误移动','kind':'file','place_id':v['other_place'],'uri':'/test.mp4','enabled':True}).status_code==403


def test_session_revocation_and_secret_not_in_db(case):
    client,login,_=case
    h=login('communityuser')
    secret=h['Authorization'].split(' ',1)[1]
    with main.db() as c:
        rows=[x['token_hash'] for x in c.execute('SELECT token_hash FROM auth_sessions')]
        assert secret not in rows
        assert len(rows[0])==64
    assert client.get('/api/auth/me',headers=h).status_code==200
    assert client.post('/api/auth/logout',headers=h).status_code==200
    assert client.get('/api/auth/me',headers=h).status_code==401


def test_restricted_sources_redact_uri(case):
    client,login,v=case
    h1=login('stateuser')
    create=client.post('/api/sources',headers=h1,json={
        'place_id':v['local_place'],'name':'测试凭证','kind':'rtsp',
        'uri':'rtsp://user:password@127.0.0.1:8554/demo'})
    assert create.status_code==200,create.text
    h2=login('countyuser')
    text=str(client.get('/api/sources',headers=h2).json())
    assert 'password' not in text
    assert '[REDACTED]' in text
    assert 'password' in str(client.get('/api/sources',headers=h1).json())


def test_excel_23_user_management_is_state_only_and_revokes_sessions(case):
    """Real auth, SQLite and tokens: four-tier role assignment & soft deletion."""
    client,login,v=case
    h=login('stateuser')
    county=login('countyuser')
    assert client.get('/api/auth/users').status_code==401
    assert client.get('/api/auth/users',headers=county).status_code==403
    assert client.post('/api/auth/users',headers=county,json={
        'username':'badcountycreate','password':'abc-strong-password','region_id':v['leaf']
    }).status_code==403
    assert client.post('/api/auth/users',headers=h,json={
        'username':'shortpass','password':'short','region_id':v['leaf']
    }).status_code==422

    created=client.post('/api/auth/users',headers=h,json={
        'username':'fourleveldemo','password':'first-strong-password-2026',
        'region_id':v['leaf']})
    assert created.status_code==201,created.text
    uid=created.json()['id']
    assert created.json()['role']=='community'
    assert client.post('/api/auth/users',headers=h,json={
        'username':'fourleveldemo','password':'other-strong-password-2026',
        'region_id':v['leaf']}).status_code==409
    items=client.get('/api/auth/users',headers=h).json()['users']
    assert any(x['id']==uid and x['region_id']==v['leaf'] for x in items)
    assert 'password_hash' not in str(items)
    assert 'first-strong-password' not in str(items)

    first=client.post('/api/auth/login',json={
        'username':'fourleveldemo','password':'first-strong-password-2026'})
    assert first.status_code==200
    oldtoken={'Authorization':'Bearer '+first.json()['access_token']}
    assert client.get('/api/auth/me',headers=oldtoken).json()['role']=='community'
    assert client.get('/api/places',headers=oldtoken).status_code==200
    assert v['other_place'] not in {p['id'] for p in client.get('/api/places',headers=oldtoken).json()}
    # A role change invalidates sessions, and the next login receives the new scope.
    updated=client.patch(f'/api/auth/users/{uid}',headers=h,json={
        'region_id':v['other_county']})
    assert updated.status_code==200,updated.text
    assert updated.json()['role']=='county'
    assert client.get('/api/auth/me',headers=oldtoken).status_code==401
    second=client.post('/api/auth/login',json={
        'username':'fourleveldemo','password':'first-strong-password-2026'})
    assert second.status_code==200
    h2={'Authorization':'Bearer '+second.json()['access_token']}
    places=client.get('/api/places',headers=h2).json()
    assert v['other_place'] in {p['id'] for p in places} and v['local_place'] not in {p['id'] for p in places}

    assert client.post(f'/api/auth/users/{uid}/reset-password',headers=county,json={
        'password':'unauthorized-password-2026'}).status_code==403
    reset=client.post(f'/api/auth/users/{uid}/reset-password',headers=h,json={
        'password':'new-strong-password-2026'})
    assert reset.status_code==200,reset.text
    assert reset.json()['sessions_revoked'] is True
    assert client.get('/api/auth/me',headers=h2).status_code==401
    assert client.post('/api/auth/login',json={
        'username':'fourleveldemo','password':'first-strong-password-2026'}).status_code==401
    assert client.post('/api/auth/login',json={
        'username':'fourleveldemo','password':'new-strong-password-2026'}).status_code==200

    assert client.patch(f'/api/auth/users/{uid}',headers=h,json={
        'enabled':False}).status_code==200
    assert client.post('/api/auth/login',json={
        'username':'fourleveldemo','password':'new-strong-password-2026'}).status_code==401
    assert client.patch(f'/api/auth/users/{uid}',headers=h,json={
        'enabled':True}).status_code==200
    assert client.post('/api/auth/login',json={
        'username':'fourleveldemo','password':'new-strong-password-2026'}).status_code==200
    audit=client.get('/api/admin/audit',headers=h).json()
    all_audit=str(audit)
    assert 'auth_user_create' in all_audit and 'auth_password_reset' in all_audit
    assert 'new-strong-password-2026' not in all_audit
    assert 'first-strong-password-2026' not in all_audit


def test_account_update_never_disables_current_state_admin(case):
    client,login,_=case
    h=login('stateuser')
    me=client.get('/api/auth/me',headers=h).json()
    assert client.patch(f"/api/auth/users/{me['id']}",headers=h,json={'enabled':False}).status_code==409
    assert client.patch(f"/api/auth/users/{me['id']}",headers=h,json={'region_id':me['region_id']}).status_code==409
    assert client.get('/api/auth/me',headers=h).status_code==200
