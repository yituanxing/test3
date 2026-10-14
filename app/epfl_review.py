"""Research-only review queue for actual EPFL synchronized anonymous pair candidates.

Imports a reproducible, SHA-pinned analysis *report*, not video or people.
Never persists official GT identities or claims to know a human's real
identity. Camera pairs stay separate from the UTC Frigate→D### research
session workflow and the synthetic P### roster/risk alerts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, Field
from app import access, admin

router = APIRouter()
_db = None

# These are the TWO actual official AVI SHA-256 values from the successful
# owner-job validation #38501951697. Pinning prevents accidental source drift,
# but it does not authenticate a user-uploaded JSON report.
CAMERA_SHA256 = (
    "0ea883222b6518a321d081b2e5570c0b02d568b5cc7cb70b6b72d4af8d50d8a9",
    "17e497340d92291793649dd378923c717eb2f7a0a419419041d9d9415f8b534f",
)
MAX_PAIR_DISTANCE = 25.0
BUNDLED_CANDIDATES = (Path(__file__).resolve().parents[1] /
                      "config" / "epfl6p_real_pair_candidates_v1.json")
MAX_BUNDLE_BYTES = 1_048_576
DATASET = "laboratory_six_person_four_synced_views"
NOT_VERIFIED = ("研究视频同步几何匹配候选；仅人工研究质检，"
                "不是现实个人身份、到访或自动预警。上传JSON的来源需单独核验。")


class PairReview(BaseModel):
    candidate_key: str = Field(pattern=r"^[0-9a-f]{32}$")
    verdict: Literal["plausible_same_target", "false_match", "needs_review"]
    reason: str = Field(min_length=8, max_length=500)


def initialize(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS epfl_candidate_reports (
       report_digest TEXT PRIMARY KEY,
       video_sha0 TEXT NOT NULL,
       video_sha1 TEXT NOT NULL,
       candidate_count INTEGER NOT NULL,
       imported_by INTEGER NOT NULL,
       imported_at TEXT NOT NULL,
       trust_level TEXT NOT NULL CHECK(trust_level='user_provided_research_report')
    );
    CREATE TABLE IF NOT EXISTS epfl_pair_candidates (
       candidate_key TEXT PRIMARY KEY,
       report_digest TEXT NOT NULL REFERENCES epfl_candidate_reports(report_digest),
       eval_split TEXT NOT NULL CHECK(eval_split IN ('baseline','holdout')),
       frame_0_based INTEGER NOT NULL,
       cam0_box_index INTEGER NOT NULL,
       cam1_box_index INTEGER NOT NULL,
       ground_distance REAL NOT NULL,
       UNIQUE(report_digest,eval_split,frame_0_based,cam0_box_index,cam1_box_index)
    );
    CREATE INDEX IF NOT EXISTS epfl_pair_candidate_by_report
       ON epfl_pair_candidates(report_digest,eval_split,frame_0_based);
    CREATE TABLE IF NOT EXISTS epfl_pair_reviews (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       candidate_key TEXT NOT NULL REFERENCES epfl_pair_candidates(candidate_key),
       verdict TEXT NOT NULL CHECK(verdict IN (
         'plausible_same_target','false_match','needs_review')),
       reason TEXT NOT NULL,
       reviewer_id INTEGER NOT NULL,
       reviewed_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS epfl_review_by_candidate
       ON epfl_pair_reviews(candidate_key,id);
    """)
    # Compatible with existing persistent databases from the manual-JSON
    # release. The old SQLite trust_level CHECK remains unchanged.
    if "origin" not in {r["name"] for r in c.execute(
            "PRAGMA table_info(epfl_candidate_reports)")}:
        c.execute("""ALTER TABLE epfl_candidate_reports ADD COLUMN origin
                   TEXT NOT NULL DEFAULT 'uploaded_research_json'""")


def register(app, db):
    global _db
    _db = db
    app.include_router(router)


def _admin(request):
    actor = access.principal(request)
    if not actor:
        raise HTTPException(401, "请先登录")
    if actor["level"] != 1:
        raise HTTPException(403, "研究素材和跨机候选审核仅州级管理员可操作")
    return actor


def _reject(message):
    raise HTTPException(status_code=422, detail=message)


def _extract(raw):
    """Validate first, then yield sanitized geometry-only candidate tuples.

    Never store or expose audit_gt_cam0/audit_gt_cam1 from uploaded ground
    truth. GT enters only previous offline benchmarking; never runtime links.
    """
    if not isinstance(raw, dict):
        _reject("研究报告格式错误")
    if (raw.get("dataset") != DATASET or
        raw.get("model") != "yolo11n.pt" or
        raw.get("matching_gt_is_never_input") is not True or
        raw.get("production_real_roster_or_alerts") is not False or
        raw.get("selected_cameras") != [0, 1] or
        raw.get("matching_algorithm") !=
          "fixed-camera calibrated-ground-plane Hungarian gate"):
        _reject("模型/算法/摄像机或研究边界不符")
    cam = raw.get("per_camera")
    if not isinstance(cam, dict) or any(
        not isinstance(cam.get(str(i)), dict) or
        cam[str(i)].get("sha256") != CAMERA_SHA256[i]
        for i in (0, 1)
    ):
        _reject("视频原始SHA与已核验官方研究素材不一致")
    records = []
    for split, key in (("baseline", "frame_audits"),
                       ("holdout", "holdout_frame_audits")):
        rows = raw.get(key)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 119:
            _reject("研究样本数量不符合验收结构")
        for row in rows:
            if not isinstance(row, dict):
                _reject("时间帧结构错误")
            frame = row.get("frame_0_based")
            pairs = row.get("candidate_evidence")
            if type(frame) is not int or not 0 <= frame < 2955:
                _reject("无效视频帧号")
            if not isinstance(pairs, list) or len(pairs) > 10:
                _reject("候选配对列表无效")
            if row.get("cross_camera_candidates") != len(pairs):
                _reject("帧内配对计数不一致")
            for p in pairs:
                if not isinstance(p, dict):
                    _reject("研究候选结构错误")
                a, b, d = (
                    p.get("cam0_model_box_index"), p.get("cam1_model_box_index"),
                    p.get("calibration_distance"))
                if (type(a) is not int or not 0 <= a < 30 or
                    type(b) is not int or not 0 <= b < 30 or
                    type(d) not in (int, float) or
                    not 0 <= d <= MAX_PAIR_DISTANCE):
                    _reject("检测框序号或几何距离不合法")
                records.append((split, frame, a, b, round(float(d), 3)))
    if not 1 <= len(records) <= 400 or len(records) != len(set(records)):
        _reject("研究候选为空或存在重复匹配")
    # Validate 1-to-1 pairing per synchronized frame.
    left, right = set(), set()
    for split, frame, a, b, d in records:
        lk, rk = (split, frame, a), (split, frame, b)
        if lk in left or rk in right:
            _reject("同一帧存在非一对一配对")
        left.add(lk); right.add(rk)
    records.sort()
    return records


def _hash(records):
    normalized = json.dumps(
        {"dataset": DATASET, "video_sha256": CAMERA_SHA256,
         "model": "yolo11n.pt", "match_distance": MAX_PAIR_DISTANCE,
         "pairs": records},
        ensure_ascii=True, separators=(",", ":"))
    return sha256(normalized.encode()).hexdigest()


@router.post("/api/research/epfl/import", status_code=201)
def import_research_report(raw: dict, request: Request):
    return _import(raw, request, "uploaded_research_json")


def _bundle():
    if not BUNDLED_CANDIDATES.is_file():
        return None
    if BUNDLED_CANDIDATES.stat().st_size > MAX_BUNDLE_BYTES:
        raise HTTPException(409, "本机候选研究包超过大小限制")
    try:
        raw = json.loads(BUNDLED_CANDIDATES.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(409, "本机研究样本损坏") from exc
    # Must verify provenance metadata AND model/calibration/video hash contract.
    if raw.get("schema_version") != "epfl6p_anonymous_candidate_bundle_v1" or (
            raw.get("generated_from") != "real_video_owner_ci_detector_result"):
        raise HTTPException(409, "这不是官方媒体CI生成的匿名候选格式")
    _extract(raw)
    return raw


@router.get("/api/research/epfl/bundled")
def bundled_availability(request: Request):
    _admin(request)
    raw = _bundle()
    if raw is None:
        return {"available": False, "candidate_count": 0,
                "reason": "源码未包含研究数据；下载owner CI构建的正式演示ZIP，或手动导入CI报告",
                "identity_verified": False, "feeds_real_alerts": False}
    records = _extract(raw)
    return {"available": True, "candidate_count": len(records),
            "report_digest": _hash(records),
            "source_ci_run_id": raw.get("source_ci_run_id"),
            "real_person_identity_verified": False,
            "feeds_roster": False, "feeds_real_alerts": False,
            "note": NOT_VERIFIED}


@router.post("/api/research/epfl/bundled/import", status_code=201)
def import_bundled(request: Request):
    _admin(request)
    raw = _bundle()
    if raw is None:
        raise HTTPException(404, "没有可导入的研究样本，需从CI演示包获取")
    return _import(raw, request, "ci_generated_research_bundle")


def _import(raw: dict, request: Request, origin: str):
    actor = _admin(request)
    data = json.dumps(raw)
    if len(data.encode("utf-8")) > 1024 * 1024:
        raise HTTPException(413, "研究报告最大1MiB")
    records = _extract(raw)
    digest = _hash(records)
    with _db() as c:
        c.execute("BEGIN IMMEDIATE")
        duplicate = c.execute(
            "SELECT 1 FROM epfl_candidate_reports WHERE report_digest=?",
            (digest,)).fetchone() is not None
        if not duplicate:
            now = datetime.now(timezone.utc).isoformat()
            c.execute("""INSERT INTO epfl_candidate_reports
            (report_digest,video_sha0,video_sha1,candidate_count,
             imported_by,imported_at,trust_level,origin)
            VALUES(?,?,?,?,?,?,'user_provided_research_report',?)""",
            (digest, *CAMERA_SHA256, len(records), actor["id"], now,origin))
            for split, frame, a, b, dist in records:
                key = sha256(
                    f"{digest}:{split}:{frame}:{a}:{b}:{dist}".encode()
                ).hexdigest()[:32]
                c.execute("""INSERT INTO epfl_pair_candidates
                 (candidate_key,report_digest,eval_split,frame_0_based,
                  cam0_box_index,cam1_box_index,ground_distance)
                 VALUES(?,?,?,?,?,?,?)""",
                 (key, digest, split, frame, a, b, dist))
            admin.audit(c,"research_epfl_candidate_import",
                        f"epfl-report:{digest}",None,
                        {"count":len(records),"origin":origin,
                         "trust":"research_candidate_not_real_identity",
                         "user":actor["id"]})
        elif origin == "ci_generated_research_bundle":
            # Same verified geometry, preserve old reviewer decisions and ID.
            c.execute("""UPDATE epfl_candidate_reports SET origin=?
                         WHERE report_digest=? AND origin<>?""",
                      (origin,digest,origin))
    return {"report_digest":digest,"candidate_count":len(records),
            "already_present":duplicate,"origin":origin,
            "real_person_identity_verified":False,
            "feeds_roster":False,"feeds_real_alerts":False,"note":NOT_VERIFIED}


@router.get("/api/research/epfl/summary")
def research_pair_summary(request: Request):
    """Operational per-candidate REVIEW COUNTS, not people/headcount/alerts.

    Latest report only; one candidate can be reviewed repeatedly but its
    current state is the last append-only review entry. Never use the
    EPFL official GT human IDs at runtime.
    """
    _admin(request)
    with _db() as c:
        latest = c.execute("""SELECT report_digest, candidate_count, origin,
                                    imported_at FROM epfl_candidate_reports
          ORDER BY imported_at DESC, rowid DESC LIMIT 1""").fetchone()
        if latest is None:
            return {
                "has_report": False, "candidate_count": 0,
                "reviewed_count": 0, "pending_count": 0,
                "plausible_count": 0, "false_match_count": 0,
                "needs_review_count": 0,
                "verified_unique_real_people": False,
                "feeds_roster": False, "feeds_real_alerts": False,
                "note": NOT_VERIFIED}
        rows = c.execute("""
          SELECT COALESCE(r.verdict,'not_reviewed') AS verdict, COUNT(*) AS n
          FROM epfl_pair_candidates p
          LEFT JOIN epfl_pair_reviews r ON r.id = (
            SELECT MAX(id) FROM epfl_pair_reviews
            WHERE candidate_key = p.candidate_key)
          WHERE p.report_digest=?
          GROUP BY COALESCE(r.verdict,'not_reviewed')
        """, (latest["report_digest"],)).fetchall()
        by = {x["verdict"]:x["n"] for x in rows}
        count = latest["candidate_count"]
        if sum(by.values()) != count:
            raise HTTPException(503, "研究报告候选计数不一致")
        pending = by.get("not_reviewed", 0)
        return {
            "has_report": True,
            "report_digest":latest["report_digest"],
            "origin": latest["origin"],
            "imported_at":latest["imported_at"],
            "candidate_count":count, "pending_count":pending,
            "reviewed_count":count-pending,
            "plausible_count":by.get("plausible_same_target",0),
            "false_match_count":by.get("false_match",0),
            "needs_review_count":by.get("needs_review",0),
            "verified_unique_real_people": False,
            "feeds_roster": False, "feeds_real_alerts": False,
            "note": NOT_VERIFIED}


@router.get("/api/research/epfl/candidates")
def candidates(request: Request,
               report_digest: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"),
               offset: int = Query(0, ge=0),
               limit: int = Query(100, ge=1, le=200)):
    _admin(request)
    with _db() as c:
        if report_digest is None:
            r = c.execute("""SELECT report_digest FROM epfl_candidate_reports
                           ORDER BY imported_at DESC, rowid DESC LIMIT 1""").fetchone()
            report_digest = r["report_digest"] if r else None
        if report_digest is None:
            return {"report_digest":None,"candidates":[],"candidate_count":0,
                    "reviewed_count":0,"next_offset":None,
                    "identity_verified":False,"note":NOT_VERIFIED}
        meta = c.execute(
            "SELECT * FROM epfl_candidate_reports WHERE report_digest=?",
            (report_digest,)).fetchone()
        if not meta:
            raise HTTPException(404, "研究报告不存在")
        rows = c.execute("""SELECT p.*,
           r.id AS review_id,r.verdict AS verdict,r.reason AS review_reason,
           r.reviewed_at AS reviewed_at
           FROM epfl_pair_candidates p LEFT JOIN epfl_pair_reviews r
             ON r.id=(SELECT MAX(id) FROM epfl_pair_reviews
                      WHERE candidate_key=p.candidate_key)
           WHERE p.report_digest=?
           ORDER BY p.frame_0_based,p.eval_split,p.cam0_box_index,p.cam1_box_index
           LIMIT ? OFFSET ?""",
           (report_digest,limit+1,offset)).fetchall()
        reviewed = c.execute("""SELECT COUNT(*) FROM epfl_pair_candidates p
               WHERE p.report_digest=? AND EXISTS
               (SELECT 1 FROM epfl_pair_reviews r
                WHERE r.candidate_key=p.candidate_key)""",
               (report_digest,)).fetchone()[0]
        return {"report_digest":report_digest,
                "candidate_count":meta["candidate_count"],
                "reviewed_count":reviewed,
                "candidates":[dict(x) for x in rows[:limit]],
                "next_offset":offset+limit if len(rows)>limit else None,
                "review_source":meta["origin"],
                "source_video_has_official_gt": True,
                "official_gt_ids_stored":False,
                "verified_unique_people":False,
                "feeds_roster":False,"feeds_real_alerts":False,
                "identity_verified":False,"note":NOT_VERIFIED}


@router.post("/api/research/epfl/reviews", status_code=201)
def review_pair(data: PairReview, request: Request):
    actor = _admin(request)
    if len(data.reason.strip()) < 8:
        raise HTTPException(422, "人工复核理由不少于8个字符")
    with _db() as c:
        c.execute("BEGIN IMMEDIATE")
        candidate = c.execute(
            "SELECT * FROM epfl_pair_candidates WHERE candidate_key=?",
            (data.candidate_key,)).fetchone()
        if candidate is None:
            raise HTTPException(404, "未找到已导入的候选")
        now = datetime.now(timezone.utc).isoformat()
        cur = c.execute("""INSERT INTO epfl_pair_reviews
             (candidate_key,verdict,reason,reviewer_id,reviewed_at)
             VALUES(?,?,?,?,?)""",
             (data.candidate_key,data.verdict,data.reason.strip(),actor["id"],now))
        admin.audit(c,"research_epfl_candidate_review",
                    f"epfl-pair:{data.candidate_key}",None,
                    {"review_id":cur.lastrowid,"verdict":data.verdict,
                     "report_digest":candidate["report_digest"],
                     "identity_verified":False})
        return {"review_id":cur.lastrowid,"candidate_key":data.candidate_key,
                "verdict":data.verdict,"reviewed_at":now,
                "identity_verified":False,"feeds_roster":False,
                "feeds_real_alerts":False,"note":NOT_VERIFIED}
