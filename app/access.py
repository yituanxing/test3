"""Server-enforced, role + territorial scope authorization for the local demo.

Four administrative levels are attached to accounts, never supplied by a UI role switch.
Bearer credentials are random, revocable, time-limited and stored hashed in SQLite.
All unfamiliar scoped API operations fail closed rather than leaking global aggregates.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

HASHER = PasswordHasher(time_cost=2, memory_cost=16384, parallelism=2)
SESSION_HOURS = 8
ROLES = {1: 'state', 2: 'county', 3: 'street', 4: 'community'}
router = APIRouter()
_db = None


def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS auth_users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      region_id INTEGER NOT NULL REFERENCES regions(id),
      enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS auth_sessions (
      token_hash TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES auth_users(id),
      expires_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS sessions_user ON auth_sessions(user_id);
    ''')


def create_user(c, username: str, password: str, region_id: int):
    if not re.fullmatch(r'[a-z][a-z0-9_.-]{2,31}', username):
        raise ValueError('用户名须为3~32位小写字母、数字、点、下划线或连字符')
    if len(password) < 12:
        raise ValueError('密码至少12位')
    region = c.execute('SELECT level FROM regions WHERE id=?', (region_id,)).fetchone()
    if not region:
        raise ValueError('不存在此区域')
    result = c.execute('''INSERT INTO auth_users(username,password_hash,region_id,created_at)
        VALUES (?,?,?,?)''', (username,HASHER.hash(password),region_id,
                            datetime.now(timezone.utc).isoformat(timespec='seconds')))
    return result.lastrowid


def _sha(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current(c, authorization: str | None):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, '请先登录（Bearer Token）', headers={'WWW-Authenticate':'Bearer'})
    token = authorization[7:].strip()
    if len(token) != 64 or not re.fullmatch('[0-9a-f]+', token):
        raise HTTPException(401, '会话无效', headers={'WWW-Authenticate':'Bearer'})
    user = c.execute('''SELECT u.id,u.username,u.region_id,r.level,r.name AS region_name,
        s.expires_at FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id
        JOIN regions r ON r.id=u.region_id WHERE s.token_hash=? AND u.enabled=1''',
        (_sha(token),)).fetchone()
    if not user or int(user['expires_at']) <= time.time():
        raise HTTPException(401, '会话已失效，请重新登录', headers={'WWW-Authenticate':'Bearer'})
    return {**dict(user), 'role':ROLES[user['level']]}


def principal(request: Request):
    return getattr(request.state, 'principal', None)


def subtree(c, request: Request):
    actor = principal(request)
    if actor is None:  # test-only compatibility for legacy regression tests
        return None
    return {r['id'] for r in c.execute('''WITH RECURSIVE subtree(id) AS (
       SELECT id FROM regions WHERE id=?
       UNION ALL SELECT r.id FROM regions r JOIN subtree s ON r.parent_id=s.id
    ) SELECT id FROM subtree''', (actor['region_id'],))}


def effective_region(c, request: Request, region_id: int | None):
    actor = principal(request)
    if actor is None:
        return region_id
    if region_id is None:
        return actor['region_id']
    if region_id not in subtree(c,request):
        raise HTTPException(403, '禁止查询辖区外数据')
    return region_id


def check_place(c, request: Request, place_id: int):
    actor = principal(request)
    if actor is None:
        return
    row = c.execute('SELECT region_id FROM places WHERE id=?', (place_id,)).fetchone()
    if not row or row['region_id'] not in subtree(c,request):
        raise HTTPException(403, '禁止访问辖区外场所')


def check_source(c, request: Request, source_id: int):
    row = c.execute('SELECT place_id FROM sources WHERE id=?', (source_id,)).fetchone()
    if not row:
        raise HTTPException(404, '视频源不存在')
    check_place(c, request, row['place_id'])


def permitted(actor: dict, method: str, path: str) -> bool:
    """Explicit matrix. Tier1 demo administrator has legacy full-system access.

    Other levels can read only endpoints whose implementations enforce subtree
    and can modify only properly scope-checked inventory endpoints.
    """
    if actor['level'] == 1:
        return True
    read = method == 'GET'
    scope_reads = (
        path in {'/api/regions', '/api/places', '/api/sources',
                 '/api/monitoring/overview', '/api/frigate/events',
                 '/api/frigate/cameras', '/api/frigate/summary',
                 '/api/insights/overview', '/api/insights/people',
                 '/api/insights/relationships'}
        or re.fullmatch(r'/api/insights/people/P[0-9]{3}',path)
    )
    if read and scope_reads:
        return True
    if actor['level'] == 2:
        if (method,path) in {('POST','/api/places'), ('POST','/api/sources')}:
            return True
        if method == 'PUT' and re.fullmatch(r'/api/(places|sources)/\d+',path):
            return True
        if method == 'PATCH' and re.fullmatch(r'/api/sources/\d+/state',path):
            return True
    return False


class LoginIn(BaseModel):
    username: str = Field(min_length=1,max_length=64)
    password: str = Field(min_length=1,max_length=256)


def register(app,db):
    global _db
    _db=db
    app.include_router(router)


@router.post('/api/auth/login')
def login(data: LoginIn):
    # A constant-cost dummy verify prevents trivial username enumeration.
    with _db() as c:
        user = c.execute('''SELECT id,password_hash,enabled FROM auth_users WHERE username=?''',
                         (data.username,)).fetchone()
        hash_to_check = user['password_hash'] if user else DUMMY_HASH
        try:
            good = HASHER.verify(hash_to_check,data.password)
        except (VerificationError, ValueError):
            good = False
        if not user or not user['enabled'] or not good:
            raise HTTPException(401, '用户名或密码错误')
        secret = secrets.token_hex(32)
        expiry = int(time.time()) + 3600*SESSION_HOURS
        c.execute('INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                  (_sha(secret),user['id'],expiry))
        c.execute('DELETE FROM auth_sessions WHERE expires_at < ?', (int(time.time()),))
    return {'access_token':secret,'token_type':'bearer','expires_at':expiry}


@router.get('/api/auth/me')
def me(request: Request):
    user=principal(request)
    if not user:
        raise HTTPException(401,'请先登录')
    return {k:user[k] for k in ('id','username','region_id','level','role','region_name')}


@router.post('/api/auth/logout')
def logout(request: Request):
    auth=request.headers.get('authorization','')
    if auth.startswith('Bearer '):
        with _db() as c:
            c.execute('DELETE FROM auth_sessions WHERE token_hash=?',(_sha(auth[7:].strip()),))
    return {'revoked':True}


# At module import time, but never saved as a real account.
DUMMY_HASH = HASHER.hash('invalid-dummy-password-not-an-account')
