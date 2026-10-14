"""Exercise an extracted, shipped demo ZIP as an actual local service.

Runs the packed source tree (not the Git checkout), real-only, bound to
127.0.0.1. Uses an ephemeral SQLite DB and a randomly generated test-only
admin login. Verifies real EPFL candidate provenance, Excel 23 items and
seven simulation rules, with hard separation of simulated and real records.
Never requires original research AVI, Docker, a mock server, or live cameras.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/"dist"/"zd-demo-local-source.zip"
REPORT=ROOT/"reports"/"local-zip-release-acceptance.json"

def _request(base,path,token=None,method="GET",body=None,expect=200):
    payload=json.dumps(body).encode() if body is not None else None
    headers={"Content-Type":"application/json"}
    if token:
        headers["Authorization"]="Bearer "+token
    req=urllib.request.Request(base+path,data=payload,method=method,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=10) as response:
            status=response.status
            raw=response.read()
    except urllib.error.HTTPError as error:
        status=error.code
        raw=error.read()
    assert status==expect, (path,status,expect,raw[:500])
    return json.loads(raw) if raw and raw.startswith(b"{") else raw.decode(errors="replace")

def main():
    assert ARCHIVE.is_file(),f"Build {ARCHIVE} first"
    with tempfile.TemporaryDirectory(prefix="zd-shipped-zip-") as temp:
        target=Path(temp)
        with ZipFile(ARCHIVE) as bundle:
            assert bundle.testzip() is None
            bundle.extractall(target)
        app_dir=target/"zd-demo"
        assert (app_dir/"scripts"/"start_local_demo.sh").is_file()
        assert (app_dir/"static"/"real.html").is_file()
        assert (app_dir/"app"/"demo_excel_alerts.py").is_file()
        assert (app_dir/"config"/"epfl6p_real_pair_candidates_v1.json").is_file(), (
            "Missing real model-derived EPFL pair bundle in deliverable")
        source=(app_dir/"static"/"real.html").read_text(encoding="utf-8")
        assert 'id="excelAcceptance"' in source
        assert 'id="excelRuleShowcase"' in source
        env={**os.environ,"ZD_DATA_DIR":str(target/"isolated_database"),
             "ZD_LEGACY_MODE":"0","PYTHONPATH":str(app_dir)}
        password=secrets.token_urlsafe(24)
        env["TEMP_QA_PASSWORD"]=password
        setup_code=(
            "from app import main,access\n"
            "import os\n"
            "main.setup_db()\n"
            "with main.db() as db:\n"
            " r=db.execute('SELECT id FROM regions WHERE level=1 ORDER BY id LIMIT 1').fetchone()\n"
            " access.create_user(db,'release_qa',os.environ['TEMP_QA_PASSWORD'],r[0])\n"
        )
        subprocess.run([sys.executable,"-c",setup_code],cwd=app_dir,env=env,
                       check=True,capture_output=True,timeout=40)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0))
            port=sock.getsockname()[1]
        base=f"http://127.0.0.1:{port}"
        server=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app",
                  "--host","127.0.0.1","--port",str(port),"--log-level","error"],
                  cwd=app_dir,env=env,stdout=subprocess.DEVNULL,
                  stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("Extracted ZIP failed to start Uvicorn: "+
                                       server.stderr.read().decode()[-1200:])
                try:
                    _request(base,"/api/health")
                    break
                except (OSError,AssertionError):
                    time.sleep(.15)
            else:
                raise RuntimeError("Extracted ZIP local server did not open")
            login=_request(base,"/api/auth/login",method="POST",
                body={"username":"release_qa","password":password})
            token=login["access_token"]
            assert "id=\"excelAcceptance\"" in _request(base,"/real",token)
            assert "id=\"epflResearchPanel\"" in _request(base,"/cctv",token)
            _request(base,"/api/real/excel-acceptance",expect=401)
            _request(base,"/api/business/analysis",token,expect=410)
            _request(base,"/api/insights/overview",token,expect=410)
            requirements=_request(base,"/api/real/excel-acceptance",token)
            assert len(requirements["requirements"])==23
            assert requirements["status_counts"]=={
                "verified_real":3,"synthetic_only":10,
                "partial":8,"missing":2}
            assert requirements["all_23_fully_accepted"] is False
            simulated=_request(base,"/api/real/excel-rule-demo",token)
            assert simulated["mode"]=="synthetic_only"
            assert simulated["based_on_real_video"] is False
            assert len({a["type"] for a in simulated["alerts"]})==7
            before=_request(base,"/api/real/activity",token)
            assert not before["ready"]
            imported=_request(base,"/api/research/epfl/bundled/import",
                              token,method="POST",body={},expect=201)
            activity=_request(base,"/api/real/activity",token)
            assert activity["ready"] is True
            assert activity["matched_pair_observations"]==119
            assert activity["multi_instant_fragments"]==14
            assert activity["cross_time_temporal_linkage_verified"] is True
            alerts=_request(base,"/api/real/demo-alerts",token)
            assert len(alerts["alerts"])==14
            assert all(a["source_evidence_is_real_video"]
                        and not a["alert_is_real_incident"]
                        for a in alerts["alerts"])
            checker_code=(
                "from app import main\n"
                "with main.db() as db:\n"
                " assert db.execute('SELECT COUNT(*) FROM synthetic_observations').fetchone()[0]==0\n"
                " assert db.execute('SELECT COUNT(*) FROM synthetic_alert_reviews').fetchone()[0]==0\n"
            )
            subprocess.run([sys.executable,"-c",checker_code],
                           cwd=app_dir,env=env,check=True,timeout=20)
            result={
                "verdict":"PASS","source":"extract_actual_distributed_zip",
                "server":"real_uvicorn_localhost_no_fake_http",
                "epfl_import":"actual_previously_YOLO_inferred_ci_bundle",
                "actual_genuine_camera_pair_observations":119,
                "independently_evaluated_one_second_fragments":14,
                "evidence_grounded_demo_alerts":14,
                "simulated_excel_rule_types":7,
                "simulated_rules_separate_from_actual_video":True,
                "original_excel_items":23,
                "original_excel_status_counts":requirements["status_counts"],
                "blocked_legacy_business_api":True,
                "synthetic_db_rows_inserted":0,
                "production_person_identification_claim":False,
                "all_excel_requirements_complete":False
            }
            REPORT.parent.mkdir(exist_ok=True,parents=True)
            REPORT.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",
                              encoding="utf-8")
            print("SHIPPED_ZIP_REAL_LOCALHOST_ACCEPTANCE="+json.dumps(
                  result,ensure_ascii=False),flush=True)
        finally:
            server.terminate()
            try:
                server.wait(timeout=7)
            except subprocess.TimeoutExpired:
                server.kill();server.wait(timeout=5)

if __name__=="__main__":
    main()
