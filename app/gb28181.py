"""Upstream WVP-GB28181-PRO adapter; NEVER implements SIP/GB28181 itself.

Original open-source WVP handles SIP REGISTER/INVITE/record playback and
ZLMediaKit demuxes GB PS/RTP into RTSP. This module maps authorized device
channels to fictional places, calls WVP's documented HTTP API, and registers
RTSP as a *real anonymous video source* in the existing SQLite pipeline.

No SIP packets, facial embeddings, person identities or actual camera footage
are stored here. An API contract test is not proof of a real GB device.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app import access, admin

router = APIRouter(prefix='/api/gb28181')
_get_db = None
GB_ID_RE = re.compile(r'^[0-9]{20}$')
STREAM_RE = re.compile(r'^[A-Za-z0-9_-]{1,100}$')


class BindChannel(BaseModel):
    place_id: int = Field(ge=1)
    device_id: str = Field(pattern=r'^[0-9]{20}$')
    channel_id: str = Field(pattern=r'^[0-9]{20}$')
    name: str = Field(min_length=1, max_length=100)


class PlaybackWindow(BaseModel):
    start_at: datetime
    end_at: datetime


def initialize(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS gb28181_channel_bindings(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      place_id INTEGER NOT NULL REFERENCES places(id),
      device_id TEXT NOT NULL,
      channel_id TEXT NOT NULL,
      name TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(device_id,channel_id)
    );
    CREATE TABLE IF NOT EXISTS gb28181_stream_sessions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      binding_id INTEGER NOT NULL REFERENCES gb28181_channel_bindings(id),
      source_id INTEGER NOT NULL REFERENCES sources(id),
      mode TEXT NOT NULL CHECK(mode IN ('live','playback')),
      upstream_stream TEXT NOT NULL,
      start_at TEXT,
      end_at TEXT,
      state TEXT NOT NULL CHECK(state IN ('active','stopped')),
      created_at TEXT NOT NULL,
      stopped_at TEXT
    );
    CREATE INDEX IF NOT EXISTS gb_sessions_by_binding
      ON gb28181_stream_sessions(binding_id,id);
    """)


def register(app, get_db):
    global _get_db
    _get_db = get_db
    app.include_router(router)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _configured():
    return bool(os.getenv('GB28181_WVP_URL','').strip() and
                os.getenv('GB28181_WVP_API_KEY','').strip())


def _wvp_get(route: str, params: dict | None = None):
    """Only fixed upstream routes and administrator-controlled GB IDs."""
    endpoint=os.getenv('GB28181_WVP_URL','').rstrip('/')
    key=os.getenv('GB28181_WVP_API_KEY','')
    if not endpoint or not key:
        raise HTTPException(503,'未配置 WVP 地址/API Key；GB28181 设备尚未接入')
    target=urlsplit(endpoint)
    if target.scheme not in ('http','https') or not target.hostname or \
            target.username is not None or target.password is not None or target.query:
        raise HTTPException(503,'WVP 地址必须是独立 http(s) API 服务地址')
    # Fixed destinations: caller never supplies URL, path or redirect target.
    try:
        response=httpx.get(endpoint+route,params=params,headers={'api-key':key},
                           timeout=35.0,follow_redirects=False,trust_env=False)
        if response.status_code!=200:
            raise HTTPException(502,f'WVP 返回 HTTP {response.status_code}')
        data=response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502,'WVP 接口不可达或返回内容无效') from exc
    if not isinstance(data,dict):
        raise HTTPException(502,'WVP 返回格式不是对象')
    if 'code' in data and data['code']!=0:
        raise HTTPException(502,'WVP 未能执行命令（业务错误码 %s）' % str(data['code'])[:20])
    return data


def _rtsp_uri(value: object):
    if not isinstance(value,str) or len(value)>2048:
        raise HTTPException(502,'WVP 未返回有效 RTSP 地址')
    u=urlsplit(value)
    if u.scheme not in ('rtsp','rtsps') or not u.hostname or \
          u.username is not None or u.password is not None or u.fragment:
        raise HTTPException(502,'WVP 返回的流不是无凭证 RTSP 地址')
    allowed={h.strip().lower() for h in
             os.getenv('GB28181_RTSP_HOSTS','localhost,127.0.0.1').split(',')
             if h.strip()}
    if u.hostname.lower() not in allowed:
        raise HTTPException(502,'WVP RTSP 地址主机不在 GB28181_RTSP_HOSTS 允许名单中')
    try:
        if u.port is not None and not 1<=u.port<=65535:
            raise ValueError('RTSP port out of range')
    except ValueError as exc:
        raise HTTPException(502,'WVP RTSP 端口无效') from exc
    return value


def _binding(conn, binding_id: int):
    row=conn.execute('SELECT * FROM gb28181_channel_bindings WHERE id=?',
                     (binding_id,)).fetchone()
    if not row:
        raise HTTPException(404,'国标设备通道尚未绑定')
    row=dict(row)
    admin._active_place(conn,row['place_id'])
    return row


def _gb_id(value: str):
    if not GB_ID_RE.fullmatch(value):
        raise HTTPException(422,'国标编码须为 20 位数字')
    return value


def _paged_records(payload, max_items=100):
    data=payload.get('data',payload)
    if not isinstance(data,dict):
        raise HTTPException(502,'设备目录结构无效')
    records=data.get('list')
    if not isinstance(records,list):
        raise HTTPException(502,'设备目录缺少分页 list 字段')
    return records[:max_items], int(data.get('total',len(records)))


@router.get('/status')
def status():
    return {'provider':'648540858/wvp-GB28181-pro + ZLMediaKit',
            'configured':_configured(),
            'registration_proven':False,
            'note':'配置状态不代表 SIP 注册、国标收流或回放已验收。'}


@router.get('/devices')
def devices(page:int=Query(1,ge=1,le=10000),count:int=Query(30,ge=1,le=100)):
    items,total=_paged_records(_wvp_get('/api/device/query/devices',
                                         {'page':page,'count':count}))
    return {'total':total,'devices':[
        {'device_id':i.get('deviceId'), 'name':i.get('name'),
         'online':i.get('online',i.get('status'))} for i in items]}


@router.get('/devices/{device_id}/channels')
def channels(device_id:str,page:int=Query(1,ge=1,le=10000),
             count:int=Query(50,ge=1,le=100)):
    device_id=_gb_id(device_id)
    items,total=_paged_records(_wvp_get(
        f'/api/device/query/devices/{device_id}/channels',
        {'page':page,'count':count}))
    return {'device_id':device_id,'total':total,'channels':[
        {'channel_id':i.get('deviceId',i.get('channelId')),
         'name':i.get('name'),'online':i.get('online',i.get('status'))}
        for i in items]}


@router.post('/bindings')
def bind(data:BindChannel,request:Request):
    with _get_db() as c:
        admin._active_place(c,data.place_id)
        access.check_place(c,request,data.place_id)
        try:
            row=c.execute('''INSERT INTO gb28181_channel_bindings
                (place_id,device_id,channel_id,name,created_at)
                VALUES(?,?,?,?,?)''',(data.place_id,data.device_id,data.channel_id,
                                       data.name.strip(),_now()))
        except __import__('sqlite3').IntegrityError as exc:
            raise HTTPException(409,'该国标设备通道已绑定；请使用原绑定') from exc
        result={'id':row.lastrowid,**data.model_dump()}
        admin.audit(c,'gb28181_bind',f"gb28181:{result['id']}",None,result)
        return result


@router.get('/bindings')
def bindings(request:Request):
    with _get_db() as c:
        allowed={p['id'] for p in admin.place_rows(c,access.effective_region(c,request,None))}
        rows=[dict(x) for x in c.execute(
            'SELECT * FROM gb28181_channel_bindings ORDER BY id')]
        return {'bindings':[x for x in rows if x['place_id'] in allowed]}


def _start(binding_id:int,mode:str,request:Request,window:PlaybackWindow|None=None):
    with _get_db() as c:
        item=_binding(c,binding_id)
        access.check_place(c,request,item['place_id'])
        device=item['device_id']
        channel=item['channel_id']
    if mode=='live':
        route=f'/api/play/start/{device}/{channel}'
        params=None
    else:
        if not window:
            raise HTTPException(422,'历史回放必须指定时间范围')
        if window.start_at.utcoffset() is None or window.end_at.utcoffset() is None:
            raise HTTPException(422,'历史回放时间必须携带时区')
        start=window.start_at.astimezone(timezone.utc)
        end=window.end_at.astimezone(timezone.utc)
        if not start<end or (end-start).total_seconds()>86400:
            raise HTTPException(422,'历史回放时间必须递增，窗口不超过24小时')
        tz=ZoneInfo(os.getenv('GB28181_DEVICE_TZ','Asia/Shanghai'))
        params={'startTime':start.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S'),
                'endTime':end.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}
        route=f'/api/playback/start/{device}/{channel}'
    payload=_wvp_get(route,params)
    stream=payload.get('data')
    if not isinstance(stream,dict):
        raise HTTPException(502,'WVP 没有产生可播放流，不能注册假设备源')
    uri=_rtsp_uri(stream.get('rtsp'))
    stream_id=stream.get('stream')
    if not isinstance(stream_id,str) or not STREAM_RE.fullmatch(stream_id):
        raise HTTPException(502,'WVP 未返回可信流 ID，无法关闭本次会话')
    # Store a fresh source for EACH actual upstream session. Historical
    # samples retain capture-time scene and source; never rebind old evidence.
    with _get_db() as c:
        current=_binding(c,binding_id)
        if current['place_id']!=item['place_id'] or current['device_id']!=device or \
                current['channel_id']!=channel:
            raise HTTPException(409,'国标绑定在点播期间发生变化')
        result=c.execute('''INSERT INTO sources(place_id,name,kind,uri,enabled)
             VALUES(?,?, 'rtsp',?,1)''',
             (item['place_id'],f"GB28181 {mode} {item['name']}",uri))
        source_id=result.lastrowid
        record=c.execute('''INSERT INTO gb28181_stream_sessions
             (binding_id,source_id,mode,upstream_stream,start_at,end_at,state,created_at)
             VALUES(?,?,?,?,?,?, 'active',?)''',(
             binding_id,source_id,mode,stream_id,
             start.isoformat() if mode=='playback' else None,
             end.isoformat() if mode=='playback' else None,_now()))
        audit={'binding_id':binding_id,'source_id':source_id,'mode':mode,
               'stream_id':stream_id}
        admin.audit(c,'gb28181_start',f"gb-session:{record.lastrowid}",None,audit)
    return {'session_id':record.lastrowid,'binding_id':binding_id,
            'source_id':source_id,'mode':mode,'state':'active',
            'source_kind':'rtsp','stream_id':stream_id,
            'note':'WVP 已返回 RTSP；是否真正能解码需实际视频源探测和采集验证。'}


@router.post('/bindings/{binding_id}/live')
def live(binding_id:int,request:Request):
    return _start(binding_id,'live',request)


@router.post('/bindings/{binding_id}/playback')
def playback(binding_id:int,data:PlaybackWindow,request:Request):
    return _start(binding_id,'playback',request,data)


@router.post('/sessions/{session_id}/stop')
def stop(session_id:int,request:Request):
    with _get_db() as c:
        row=c.execute('''SELECT ss.*,b.device_id,b.channel_id,b.place_id
            FROM gb28181_stream_sessions ss
            JOIN gb28181_channel_bindings b ON b.id=ss.binding_id
            WHERE ss.id=?''',(session_id,)).fetchone()
        if not row:
            raise HTTPException(404,'流会话不存在')
        session=dict(row)
        access.check_place(c,request,session['place_id'])
        if session['state']=='stopped':
            return {'session_id':session_id,'state':'stopped','repeated':True}
    device,channel=session['device_id'],session['channel_id']
    if session['mode']=='live':
        route=f'/api/play/stop/{device}/{channel}'
    else:
        route=f"/api/playback/stop/{device}/{channel}/{session['upstream_stream']}"
    _wvp_get(route)
    with _get_db() as c:
        c.execute("UPDATE gb28181_stream_sessions SET state='stopped',stopped_at=? WHERE id=?",
                  (_now(),session_id))
        c.execute('UPDATE sources SET enabled=0 WHERE id=?',(session['source_id'],))
        admin.audit(c,'gb28181_stop',f"gb-session:{session_id}",None,
                    {'session_id':session_id,'source_id':session['source_id']})
    return {'session_id':session_id,'state':'stopped','source_enabled':False}


@router.get('/sessions')
def sessions(request:Request,limit:int=Query(50,ge=1,le=250)):
    with _get_db() as c:
        allowed={p['id'] for p in admin.place_rows(c,access.effective_region(c,request,None))}
        rows=[dict(x) for x in c.execute('''SELECT ss.id,ss.binding_id,ss.source_id,
            ss.mode,ss.state,ss.created_at,ss.stopped_at,ss.start_at,ss.end_at,
            b.place_id,b.device_id,b.channel_id,b.name
            FROM gb28181_stream_sessions ss
            JOIN gb28181_channel_bindings b ON b.id=ss.binding_id
            ORDER BY ss.id DESC''')]
        return {'sessions':[x for x in rows if x['place_id'] in allowed][:limit]}
