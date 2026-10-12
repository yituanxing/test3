#!/usr/bin/env bash
# One-off, real GB28181 SIP evidence, not a synthetic HTTP success response.
# Upstream WVP-PRO runs its SIP listener; upstream Go SIP device sends real
# REGISTER/401-Digest/REGISTER and Catalog. Query WVP *SQLite?* No: MySQL DB.
# Does NOT claim PS/RTP video reception or historical playback.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p reports
BASE="$(mktemp -d)"
PROJECT="zdgbsip"
WVP_REV="b16439ac04eae982951e2edf2ee954a57d9ac4ef"
SIM_REV="e2a6097e5af9efcc719a64d1276f6d4551585142"
DEVICE="34020000001320000001"
CHANNEL="34020000001320000002"
SIM_PID=""
PASSPHRASE="$(python -c 'import secrets;print(secrets.token_hex(18))')"
REPORT="$PWD/reports/verify-gb28181-real-sip.json"
export REPORT WVP_REV SIM_REV DEVICE CHANNEL PROJECT
cleanup() {
  rc=$?
  trap - EXIT
  [ -z "$SIM_PID" ] || kill "$SIM_PID" 2>/dev/null || true
  if [ "$rc" -ne 0 ]; then
    # Only show stage names and the relevant SIP success markers, never
    # full upstream logs: WVP and simulator may emit credentials/SDP.
    echo "GB-SIP protocol verification failed; inspect generated report." >&2
  fi
  if [ -f "$BASE/wvp/docker/docker-compose.yml" ]; then
    docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml" \
      down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  # Upstream MySQL/Redis write files as container root/mysql; a normal
  # runner rm floods stderr with permission errors and hides SIP failures.
  sudo rm -rf -- "$BASE" >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup EXIT

python - <<'PY'
import json,os
from pathlib import Path
out={"verdict":"FAIL","protocol":"GB28181 SIP device simulation against real WVP",
     "server":"648540858/wvp-GB28181-pro",
     "server_commit":os.environ["WVP_REV"],
     "simulator":"lzh2nix/gb28181Simulator",
     "simulator_commit":os.environ["SIM_REV"],
     "live_sip_registration":False,
     "wvp_persisted_online_device":False,
     "catalog_channel_received":False,
     "sip_invite_rtp_ps_video":"NOT_TESTED",
     "recorded_playback":"NOT_TESTED",
     "api_contract_double_used":False}
Path(os.environ["REPORT"]).write_text(json.dumps(out,indent=2),encoding="utf-8")
PY

echo "::group::Fetch pinned independent upstream SIP server and simulator"
git clone -q https://github.com/648540858/wvp-GB28181-pro.git "$BASE/wvp"
git -C "$BASE/wvp" checkout -q "$WVP_REV"
git clone -q https://github.com/lzh2nix/gb28181Simulator.git "$BASE/sim"
git -C "$BASE/sim" checkout -q "$SIM_REV"
test "$(git -C "$BASE/wvp" rev-parse HEAD)" = "$WVP_REV"
test "$(git -C "$BASE/sim" rev-parse HEAD)" = "$SIM_REV"
echo "Upstream source revisions pinned."
echo "::endgroup::"

# WVP compose default MySQL passwords are permitted only in this disposable
# isolated ephemeral CI, NEVER copied to a production or public deployment.
# HTTP and SIP are bound to the runner's local interface; Docker bridge
# containers can still address one another on their internal network.
sed -i 's@"18978:18978"@"127.0.0.1:18978:18978"@' "$BASE/wvp/docker/docker-compose.yml"
cat > "$BASE/wvp/docker/.env" <<EOF
MediaRtmp=10001
MediaRtsp=10002
MediaRtp=10003
WebHttp=8080
WebHttps=8081
Stream_IP=127.0.0.1
SDP_IP=127.0.0.1
SIP_ShowIP=127.0.0.1
SIP_Port=18160
SIP_Domain=3402000000
SIP_Id=34020000002000000001
SIP_Password=$PASSPHRASE
RecordSip=false
RecordPushLive=false
EOF
# No external host ports are needed for media if only REGISTER and Catalog
# are being tested. Do not advertise this as an RTP media test.
echo "::group::Build/start ACTUAL WVP-PRO + MySQL/Redis/ZLMediaKit"
docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml" \
  up -d --build polaris-wvp
echo "::endgroup::"

# WVP may require schema migrations after MySQL health succeeds; readiness is
# determined via a real HTTP response and the actual initialization tables.
echo "::group::Confirm WVP server initialized its own database and SIP socket"
for n in $(seq 1 90); do
  if curl -sS --max-time 3 -o /dev/null \
      http://127.0.0.1:18978/api/device/query/devices \
      && docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml" \
        exec -T polaris-mysql mysql -uwvp_user -pwvp_password -N wvp \
        -e 'SELECT COUNT(*) FROM wvp_device' >/dev/null 2>&1; then
    break
  fi
  if [ "$n" -eq 90 ]; then
    echo "WVP startup: no HTTP+database readiness within budget" >&2
    exit 1
  fi
  sleep 3
done
echo "Actual WVP HTTP+MySQL tables initialized."
echo "::endgroup::"

echo "::group::Start independent upstream GB28181 SIP user agent"
cat > "$BASE/sim/sim-ci.conf" <<EOF
{
  "localSipPort":18161,
  "serverID":"34020000002000000001",
  "realm":"3402000000",
  "serverAddr":"127.0.0.1:18160",
  "userName":"$DEVICE",
  "password":"$PASSPHRASE",
  "regExpire":3600,
  "keepaliveInterval":25,
  "maxKeepaliveRetry":4,
  "transport":"udp",
  "gbId":"$DEVICE",
  "devices":[
    {"deviceID":"$CHANNEL","name":"licensed-demo-channel",
      "manufacturer":"CI-GB-SIP-Simulator","model":"virtual",
      "CivilCode":"340200","address":"127.0.0.1",
      "parental":"0","safeWay":"1","registerWay":"1",
      "secrecy":"0","status":"ON"}
  ]
}
EOF
# Do not print the simulator's configuration or logs: it may echo the password.
(cd "$BASE/sim" && go run main.go -c sim-ci.conf > "$BASE/simulator.log" 2>&1) &
SIM_PID=$!
echo "::endgroup::"

echo "::group::Verify real WVP DB online flag AND Catalog channel"
passed=0
VALUE="no_database_response"
for n in $(seq 1 55); do
  kill -0 "$SIM_PID" 2>/dev/null || {
    echo "GB SIP simulator exited before registration" >&2;exit 1;
  }
  # One SQL truth query checks both the online REGISTER state and the catalog
  # channel. We do not rely on a SIP ACK generated by test mocks.
  SQL="SELECT CONCAT(COUNT(DISTINCT d.device_id), ':', COUNT(DISTINCT c.gb_device_id)) FROM wvp_device d LEFT JOIN wvp_device_channel c ON c.device_id=d.device_id AND c.gb_device_id='$CHANNEL' WHERE d.device_id='$DEVICE' AND d.on_line=1;"
  VALUE="$(docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml" \
    exec -T polaris-mysql mysql -uwvp_user -pwvp_password -N wvp \
    -e "$SQL" 2>/dev/null || true)"
  if [ "$VALUE" = '1:1' ]; then
    passed=1
    break
  fi
  sleep 3
done
if [ "$passed" -ne 1 ]; then
  # Distinguish not registered, registered but offline, and Catalog missing.
  # Never print raw WVP or Go SIP logs: they can expose Authorization hashes
  # and platform/device credentials. Only collect aggregate diagnostics.
  SQL_STATE="SELECT CONCAT(COALESCE((SELECT COUNT(*) FROM wvp_device WHERE device_id='$DEVICE'),0),':',COALESCE((SELECT MAX(on_line) FROM wvp_device WHERE device_id='$DEVICE'),-1),':',COALESCE((SELECT COUNT(*) FROM wvp_device_channel WHERE device_id='$DEVICE'),0),':',COALESCE((SELECT COUNT(*) FROM wvp_device_channel WHERE device_id='$DEVICE' AND gb_device_id='$CHANNEL'),0));"
  DB_STATE="$(docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml" exec -T polaris-mysql mysql -uwvp_user -pwvp_password -N wvp -e "$SQL_STATE" 2>/dev/null || true)"
  export DB_STATE
  echo "WVP persisted SIP state (device rows:online flag:channel rows:matching channel) = ${DB_STATE:-unavailable}" >&2
  python - "$BASE" <<'PY'
import json,os,re,sys
from pathlib import Path
base=Path(sys.argv[1])
path=Path(os.environ['REPORT'])
v=json.loads(path.read_text(encoding='utf-8'))
v['failure_stage']='WVP_SIP_REGISTER_OR_CATALOG_DATABASE_ASSERTION'
v['db_counts_device_online_channels_matching']=os.getenv('DB_STATE','unavailable')
sim=base/'simulator.log'
if sim.exists():
    # Counts only. Avoid dumping SIP authorization, config or passwords.
    data=sim.read_text(encoding='utf-8',errors='replace')
    v['simulator_log_signal_counts']={
        'register_mentions':len(re.findall('register',data,re.I)),
        'digest_challenges':len(re.findall('401|WWWAuth',data,re.I)),
        'success_mentions':len(re.findall('200|registered',data,re.I)),
        'error_mentions':len(re.findall('error|failed|panic',data,re.I)),
    }
logs=list((base/'wvp/docker/logs/wvp').glob('sip*.log'))
if logs:
    data='\n'.join(p.read_text(encoding='utf-8',errors='replace') for p in logs)
    v['server_sip_signal_counts']={
        'register_mentions':len(re.findall('REGISTER|注册',data,re.I)),
        'auth_failures':len(re.findall('401|鉴权失败|认证失败',data,re.I)),
        'catalog_mentions':len(re.findall('Catalog|目录',data,re.I)),
    }
path.write_text(json.dumps(v,indent=2,ensure_ascii=False),encoding='utf-8')
print('Diagnostic stage:',v['failure_stage'])
print('Diagnostic signal counts:',json.dumps({k:v[k] for k in v if k.endswith('_signal_counts')},ensure_ascii=False))
PY
  echo "GB SIP REGISTER or Catalog not proven; WVP database requirements failed" >&2
  exit 1
fi
echo "SUCCESS: WVP persisted 1 online SIP device and its advertised Catalog channel."
echo "::endgroup::"

python - <<'PY'
import json,os
from datetime import datetime,timezone
from pathlib import Path
path=Path(os.environ["REPORT"])
v=json.loads(path.read_text(encoding="utf-8"))
v.update({"verdict":"PASS","live_sip_registration":True,
          "wvp_persisted_online_device":True,"catalog_channel_received":True,
          "check_time_utc":datetime.now(timezone.utc).isoformat(),
          "evidence":"WVP live MySQL wvp_device.on_line=1 AND wvp_device_channel.gb_device_id matches; independent upstream GB SIP simulator running",
          "note":"Real SIP control-plane only; no GB PS/RTP decoded or historical playback."})
path.write_text(json.dumps(v,indent=2,ensure_ascii=False),encoding="utf-8")
print("Real upstream GB28181 SIP verdict:",v["verdict"])
PY
