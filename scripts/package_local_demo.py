"""Package a local-only demo source archive without data, sessions or secrets."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import json

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DIST.mkdir(exist_ok=True)
TARGET = DIST / "zd-demo-local-source.zip"
FOLDERS = ("app", "static", "scripts", "samples", "deploy", "config", "tests")
FILES = (
    "README.md", "THIRD_PARTY.md", "DEMO_INTEGRATION.md",
    "PRODUCT_FLOW_V1.3.md", "ACCESS_CONTROL_V1.2.md",
    "RULE_AUDIT_V1.1.md", "BASE_REGISTRY_V0.9.md",
    "CORE_VIDEO_ROSTER_SPEC_V1.md", "DELIVERY_SCOPE_20261013.md",
    "SHARED_BUSINESS_MODEL_V1.md",
    "ANIME_SCENE_MAP_V0.1.md",
    "ANIME_EXCEL_SITE_MODEL_V1.md",
    "REAL_CCTV_PIVOT_20261013.md",
    "SHARED_MULTI_SITE_PERSON_REGISTRY_ADR_V1.md",
    "ANONYMOUS_OCCURRENCES_V1.md",
    "ANONYMOUS_QUALITY_GATE_V1.md",
    "INSIGHTS_V1.0.md", "UI_ACCEPTANCE_V1.4.md",
    "requirements.txt", "requirements-business.txt", "requirements-tracking.txt",
)
EXTENSIONS = {".py", ".html", ".sh", ".yml", ".yaml", ".md", ".txt", ".json", ".mp4"}
BANNED = {".git", ".venv", "data", "__pycache__", ".ci-data", "reports", "dist", "node_modules"}
files = []
for folder in FOLDERS:
    for path in (ROOT / folder).rglob("*"):
        if path.is_file() and path.suffix.lower() in EXTENSIONS:
            if not (set(path.relative_to(ROOT).parts) & BANNED):
                files.append(path)
for filename in FILES:
    path = ROOT / filename
    if not path.is_file():
        raise SystemExit("Missing release file: " + filename)
    files.append(path)
assert not any(path.name.startswith(".env") for path in files)
with ZipFile(TARGET, "w", ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(files):
        archive.write(path, "zd-demo/" + path.relative_to(ROOT).as_posix())
with ZipFile(TARGET) as archive:
    assert archive.testzip() is None
    assert "zd-demo/scripts/start_local_demo.sh" in archive.namelist()
    assert not any(
        set(Path(name).parts) & BANNED for name in archive.namelist()
    )
print(json.dumps({
    "file": str(TARGET), "items": len(files), "bytes": TARGET.stat().st_size,
    "contains_local_database": False, "contains_secret_tokens": False,
    "offline_installable": False
}, ensure_ascii=False))
