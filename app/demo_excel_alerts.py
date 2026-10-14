"""Excel #6–12 warning demonstration, deliberately separate from real media.

Runs the EXISTING validated seven-rule business evaluator on an isolated
in-memory classroom scenario. Does not insert people, visits, places or
alerts into the real SQLite, and cannot impersonate Frigate/EPFL events.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from fastapi import APIRouter, Request
from app import business, epfl_review

router = APIRouter()


def demo_excel_rule_analysis():
    now = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)
    examples = [
        # Each synthetic person gets an unequivocal first / home site.
        ("P001", 1, 0, 9, 0), ("P002", 1, 0, 9, 1),
        ("P003", 2, 0, 9, 2), ("P004", 3, 0, 9, 3),
        ("P005", 1, -4, 9, 0),
        # Excel #6 + #7: same A cohort twice at B, in separate windows.
        ("P001", 2, 0, 11, 0), ("P002", 2, 0, 11, 2),
        ("P001", 2, 0, 11, 40), ("P002", 2, 0, 11, 42),
        # Excel #8: members of A and B together at their respective homes.
        ("P001", 2, 0, 12, 30), ("P003", 2, 0, 12, 32),
        ("P001", 1, 0, 14, 0), ("P003", 1, 0, 14, 2),
        # Excel #9: A-person + A-person + B-person jointly at C.
        ("P001", 3, 0, 15, 0), ("P002", 3, 0, 15, 2),
        ("P003", 3, 0, 15, 4),
    ]
    rows = []
    for idx, (person, place, day, h, minute) in enumerate(examples, 1):
        time = (now.replace(hour=0, minute=0) +
                timedelta(days=day,hours=h,minutes=minute))
        rows.append({
            "id":idx, "event_key":f"SIM-RULE-{idx:03d}",
            "person_label":person, "place_id":place,
            "observed_at":time.isoformat()
        })
    config = {**business.DEFAULT_CONFIG,
              "enabled_rules":list(business.RULE_NAMES),
              "watch_place_ids":[2],
              "companion_min_episodes":2,
              "interaction_min_rounds":1,
              "gathering_min_people":3,
              "gathering_min_episodes":1,
              "headcount_difference":1,
              "activity_end_hour":18,
              "affiliation_mode":"earliest"}
    data = business.analyze(rows,config,now=now)
    data["source"] = "self_contained_fake_people_and_visits_for_excel_rule_walkthrough"
    data["based_on_real_video"] = False
    data["real_incidents"] = False
    data["time_basis"] = "fixed synthetic business clock 2026-10-14 20:00 UTC"
    data["fictional_sites"] = {"1":"演示场所 A","2":"演示场所 B",
                                 "3":"演示场所 C"}
    data["no_mutation_to_real_database"] = True
    data["summary"] = ("七种原 Excel 预警规则在旧有纯算法上演练。全部P###和"
                       "到访事件虚构，不是EPFL人体识别结果。")
    return data


@router.get("/api/real/excel-rule-demo")
def excel_rule_demo(request: Request):
    epfl_review._admin(request)
    return demo_excel_rule_analysis()


# Routes usable in the public, REAL-ONLY distribution. The old portal,
# business, insights and roster pages are intentionally disabled there.
_READ_ONLY_ROUTES = {
    1:"/cctv", 2:"/admin", 3:"/real#excelRuleShowcase",
    4:"/real", 5:"/real", 6:"/real#excelRuleShowcase",
    7:"/real#excelRuleShowcase", 8:"/real#excelRuleShowcase",
    9:"/real#excelRuleShowcase", 10:"/real#excelRuleShowcase",
    11:"/real#excelRuleShowcase", 12:"/real#excelRuleShowcase",
    13:"/cctv", 14:"/real", 15:"/real",
    16:"/cctv", 17:"/real", 18:"/real",
    19:"/real#researchActivity", 20:"/admin",
    21:"/real#excelRuleShowcase", 22:"/real#excelRuleShowcase",
    23:"/admin",
}


@router.get("/api/real/excel-acceptance")
def real_excel_acceptance(request: Request):
    """Truthful, 23-item original Excel checklist, not a feature PASS claim."""
    epfl_review._admin(request)
    manifest=json.loads((Path(__file__).resolve().parents[1] /
        "config/excel_acceptance_v1.json").read_text(encoding="utf-8"))
    req=manifest["requirements"]
    if [x["id"] for x in req]!=list(range(1,24)):
        raise RuntimeError("Incomplete Excel 23-item original acceptance contract")
    counts={key:sum(x["status"]==key for x in req) for key in
        ("verified_real","synthetic_only","partial","missing")}
    return {
        "source_file":manifest["source_file"],
        "source_worksheet":manifest["source_worksheet"],
        "source_rows":manifest["source_rows"],
        "status_counts":counts,
        "requirements":[{
            "id":x["id"],"title":x["title"],"status":x["status"],
            "delivered_now":x["delivered_now"],
            "remaining_gap":x["remaining_gap"],
            "alternative_used":x["alternative_used"],
            "not_equivalent_to":x["not_equivalent_to"],
            "demo_route":_READ_ONLY_ROUTES[x["id"]]
        } for x in req],
        "real_only":True,
        "all_23_fully_accepted":False,
        "note":"原23项按来源逐条说明；演示入口可打开不等于该条原始要求已经达标。"
    }


def register(app):
    app.include_router(router)
