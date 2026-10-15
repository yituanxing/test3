"""Manual, AUDITED research-only association of anonymous camera-local evidence.

NOT face recognition or verified identification of a natural person. This
optional, top-tier-only bridge demonstrates Excel's Visit grouping against
operator-asserted fictional alias D###, without injecting unverified real
detections into P### roster, rules or official independent-person counts.

Every link binds to the existing immutable event and its current accepted QA
evidence signature. Any upstream event/QA revision deactivates the linked
fact until explicitly re-reviewed AND re-linked; revocation is append-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field

from app import access, admin, occurrences, occurrence_reviews

router = APIRouter()
_db = None

LINK_NOTE = "这是人工声明的匿名研究分组，不是生物识别、自然人身份证明或真实预警输入。"


class ResearchLinkInput(BaseModel):
    occurrence_key: str = Field(pattern=r"^[0-9a-f]{24}$")
    alias: str = Field(pattern=r"^D[0-9]{3}$")
    action: Literal["attach", "detach"]
    reason: str = Field(min_length=8, max_length=500)


def initialize(c):
    c.executescript("""
      CREATE TABLE IF NOT EXISTS research_occurrence_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        occurrence_key TEXT NOT NULL,
        alias TEXT NOT NULL CHECK(alias GLOB 'D[0-9][0-9][0-9]'),
        action TEXT NOT NULL CHECK(action IN ('attach','detach')),
        source_signature TEXT NOT NULL,
        reason TEXT NOT NULL,
        reviewer_id INTEGER NOT NULL REFERENCES auth_users(id),
        reviewed_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS research_link_latest
        ON research_occurrence_links(occurrence_key,id);
    """)


def register(app,db):
    global _db
    _db=db
    app.include_router(router)


def _assert_admin(request):
    actor = access.principal(request)
    if not actor:
        raise HTTPException(401, "请先登录")
    if actor["level"] != 1:
        raise HTTPException(403, "研究用跨镜头人工关联仅州级管理员可访问")
    return actor


def _date(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError("missing zone")
        return date.astimezone(timezone.utc)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(422,"Frigate事件缺少可信UTC时刻") from exc


def _current_row(c,key):
    row=c.execute("""SELECT * FROM research_occurrence_links
                  WHERE occurrence_key=? ORDER BY id DESC LIMIT 1""",(key,)).fetchone()
    return dict(row) if row else None


def _status(c,record,occ):
    """Do not let an old manual assertion outlive corrected source evidence."""
    if record["action"] == "detach":
        return "detached"
    if occ is None:
        return "source_missing"
    if occ["provider"] != "frigate" or occ.get("binding_epoch_id") is None:
        return "unsupported_source"
    state = occurrence_reviews._state(c,occ)
    if state["quality_status"] != "valid_local_target":
        return "quality_not_accepted"
    if record["source_signature"] != state["evidence_signature"]:
        return "stale_evidence"
    return "active_manual_assertion"


@router.post("/api/research/links",status_code=201)
def record_link(data:ResearchLinkInput,request:Request):
    actor=_assert_admin(request)
    if len(data.reason.strip())<8:
        raise HTTPException(422,"人工关联依据至少8个字符")
    with _db() as c:
        c.execute("BEGIN IMMEDIATE")
        allowed=occurrences._allowed(c,request)
        occ=occurrences.occurrence_by_key(c,allowed,data.occurrence_key)
        if not occ:
            raise HTTPException(404,"授权范围内未找到局部目标事件")
        if occ["provider"]!="frigate":
            raise HTTPException(422,"历史CAVIAR帧号没有可信UTC时钟，不可生成现实时间到访")
        if occ.get("binding_epoch_id") is None:
            raise HTTPException(409,"必须有事件发生时的可信摄像机位置版本")
        current=_current_row(c,data.occurrence_key)
        if data.action=="attach":
            state=occurrence_reviews._state(c,occ)
            if state["quality_status"]!="valid_local_target":
                raise HTTPException(409,"目标质量尚未审核通过或原审核已失效")
            _date(occ["first_seen"])
            sig=state["evidence_signature"]
        else:
            if current is None or current["action"]!="attach" or current["alias"]!=data.alias:
                raise HTTPException(409,"当前没有对应的可撤销关联")
            sig=occurrence_reviews._signature(c,occ)
        now=datetime.now(timezone.utc).isoformat(timespec="microseconds")
        added=c.execute("""INSERT INTO research_occurrence_links
          (occurrence_key,alias,action,source_signature,reason,reviewer_id,reviewed_at)
          VALUES (?,?,?,?,?,?,?)""",
          (data.occurrence_key,data.alias,data.action,sig,
           data.reason.strip(),actor["id"],now))
        admin.audit(c,"research_manual_link",f"research-occurrence:{data.occurrence_key}",
                    {"alias":current["alias"],"action":current["action"]}
                      if current else None,
                    {"alias":data.alias,"action":data.action,
                     "review_id":added.lastrowid,"scope_place_id":occ["venue_id"],
                     "basis":"operator_assertion_not_identity_verified"})
        return {"review_id":added.lastrowid,"occurrence_key":data.occurrence_key,
                "alias":data.alias,"action":data.action,
                "effective_status":_status(c,_current_row(c,data.occurrence_key),occ),
                "identity_verified":False,"eligible_for_real_alerts":False,
                "note":LINK_NOTE}


def _reviewed_state(c,request):
    all_events={x["occurrence_key"]:x for x in occurrences.iter_occurrences(
        c,occurrences._allowed(c,request))}
    last=c.execute("""SELECT h.* FROM research_occurrence_links h
             JOIN (SELECT occurrence_key,MAX(id) AS id
                 FROM research_occurrence_links GROUP BY occurrence_key) latest
               ON h.id=latest.id ORDER BY h.id""").fetchall()
    rows=[]
    for raw in last:
        record=dict(raw)
        event=all_events.get(record["occurrence_key"])
        status=_status(c,record,event)
        rows.append({
          "occurrence_key":record["occurrence_key"],
          "alias":record["alias"],"action":record["action"],
          "latest_review_id":record["id"],
          "reviewed_at":record["reviewed_at"],
          "evidence_status":status,
          "provider":event["provider"] if event else None,
          "camera":event["camera_key"] if event else None,
          "place_id":event["venue_id"] if event else None,
          "place_name":event["venue_name"] if event else None,
          "first_seen":event["first_seen"] if event else None,
          "binding_epoch_id":event.get("binding_epoch_id") if event else None,
          "identity_verified":False,
        })
    return rows


@router.get("/api/research/links")
def links(request:Request):
    _assert_admin(request)
    with _db() as c:
        records=_reviewed_state(c,request)
        return {"links":records,"active":sum(r["evidence_status"]=="active_manual_assertion"
                 for r in records),"identity_verified":False,
                "eligible_for_real_alerts":False,"note":LINK_NOTE}


@router.get("/api/research/visits")
def candidate_visits(request:Request,gap_minutes:int=Query(default=20,ge=1,le=240)):
    """Visit sessions are AUDITED MANUAL DEMO PROJECTIONS, not real arrivals."""
    _assert_admin(request)
    with _db() as c:
        links=_reviewed_state(c,request)
        groups={}
        for record in links:
            if record["evidence_status"]!="active_manual_assertion":
                continue
            groups.setdefault((record["alias"],record["place_id"]),[]).append(record)
        visits=[]
        for (alias,place),records in groups.items():
            records.sort(key=lambda r:(_date(r["first_seen"]),r["occurrence_key"]))
            sessions=[]
            for record in records:
                moment=_date(record["first_seen"])
                if not sessions or moment-sessions[-1]["last"]>timedelta(
                        minutes=gap_minutes):
                    sessions.append({"first":moment,"last":moment,
                                     "events":[record["occurrence_key"]],
                                     "cameras":{record["camera"]}})
                else:
                    last=sessions[-1]
                    last["last"]=max(last["last"],moment)
                    last["events"].append(record["occurrence_key"])
                    last["cameras"].add(record["camera"])
            for session in sessions:
                visits.append({
                    "research_alias":alias,"place_id":place,
                    "place_name":records[0]["place_name"],
                    "start_utc":session["first"].isoformat(),
                    "end_utc":session["last"].isoformat(),
                    "evidence_occurrence_keys":session["events"],
                    "camera_count":len(session["cameras"]),
                    "local_occurrence_count":len(session["events"]),
                    "manual_research_only":True,
                    "real_person_identity_verified":False,
                    "eligible_for_real_alerts":False,
                    "eligible_for_roster":False,
                })
        visits.sort(key=lambda x:(x["research_alias"],x["start_utc"],x["place_id"]))
        return {"mode":"manual_research_candidate_visits",
                "visits":visits,"manual_assertions":len(groups),
                "excluded_links":len(links)-sum(
                    x["evidence_status"]=="active_manual_assertion" for x in links),
                "not_unique_real_people":True,
                "counts_as_verified_site_visits":False,
                "feeds_synthetic_P_roster":False,
                "feeds_real_alerts":False,
                "note":LINK_NOTE}
