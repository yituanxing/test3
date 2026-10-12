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
SIM_CONTAINER="zdgb-sim-device"
PASSPHRASE="$(python -c 'import secrets;print(secrets.token_hex(18))')"
REPORT="$PWD/reports/verify-gb28181-real-sip.json"
export REPORT WVP_REV SIM_REV DEVICE CHANNEL PROJECT
cleanup() {
  rc=$?
  trap - EXIT
  docker logs "$SIM_CONTAINER" > "$BASE/simulator.log" 2>&1 || true
  docker rm -f "$SIM_CONTAINER" >/dev/null 2>&1 || true
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
# The upstream reference simulator has two strict-parser limitations which
# some real WVP versions exercise: MIME content type is matched with exact
# capitalization, and the UDP reader hard-limits datagrams to 1500 bytes.
# Patch ONLY this disposable pinned test fixture, not our application or the
# GB28181 protocol implementation.
python - "$BASE/sim" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
ua=root/'internal/useragent/ua.go'
old='m.Payload.ContentType() == "Application/MANSCDP+xml"'
new='strings.HasPrefix(strings.ToLower(m.Payload.ContentType()), "application/manscdp+xml")'
content=ua.read_text()
if content.count(old)!=1:raise SystemExit('Simulator MIME parser upstream version changed')
ua.write_text(content.replace(old,new))
transport=root/'internal/transport/transport.go'
content=transport.read_text()
old='const sipMaxPacketSize = 1500'
if content.count(old)!=1:raise SystemExit('Simulator UDP packet bound upstream version changed')
transport.write_text(content.replace(old,'const sipMaxPacketSize = 65535'))
print('Pinned upstream simulator patched for case-insensitive MANSCDP MIME and full UDP datagrams.')
PY
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
# Isolated, non-public CI-only WVP control-plane: disable only WVP HTTP
# login enforcement so Catalog-sync can be requested from loopback without
# seeding a privileged administrator password. Crucially, GB28181 SIP Digest
# auth stays ENABLED with the randomly generated SIP_Password above.
# Production deployments MUST keep interface-authentication=true.
sed -i 's/interface-authentication: true/interface-authentication: false/' \
  "$BASE/wvp/docker/wvp/wvp/application-docker.yml"
grep -q 'interface-authentication: false' \
  "$BASE/wvp/docker/wvp/wvp/application-docker.yml"
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
  "serverAddr":"polaris-wvp:18160",
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
# SIP Via/Contact must advertise a ROUTABLE IP from the same Docker network.
# A host-namespace 127.0.0.1 Contact can silently swallow 401 replies inside
# WVP's container even while the host->container UDP packet appears sent.
# Run the actual upstream Go simulator in the official compose project network.
docker network inspect "${PROJECT}_media-net" >/dev/null
docker run -d --rm --name "$SIM_CONTAINER" \
  --network "${PROJECT}_media-net" \
  --mount "type=bind,src=$BASE/sim,dst=/app" \
  --workdir /app golang:1.23 \
  go run main.go -c sim-ci.conf >/dev/null
echo "GB simulated device is in the WVP Docker bridge network, no host-loopback SIP Contact."
echo "::endgroup::"

echo "::group::Explicitly request WVP Catalog sync AFTER actual SIP REGISTER"
registered=0
for n in $(seq 1 25); do
  DEVICE_ONLINE="$(docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml" \
    exec -T polaris-mysql mysql -uwvp_user -pwvp_password -N wvp \
    -e "SELECT COUNT(*) FROM wvp_device WHERE device_id='$DEVICE' AND on_line=1" \
    2>/dev/null || true)"
  if [ "$DEVICE_ONLINE" = 1 ]; then registered=1; break; fi
  sleep 2
done
if [ "$registered" -ne 1 ]; then
  echo "SIP REGISTER still missing, cannot legitimately trigger Catalog" >&2
  exit 1
fi
echo "WVP independently persisted online device: 1."
# WVP DeviceQuery.java exposes explicit Catalog sync: it is not guaranteed
# to send a Catalog query merely upon receiving a SIP REGISTER.
SYNC_HTTP="$(curl -sS --max-time 15 -o "$BASE/wvp-catalog-sync-response.json" -w '%{http_code}' \
  "http://127.0.0.1:18978/api/device/query/devices/$DEVICE/sync" || true)"
if [ "$SYNC_HTTP" != 200 ]; then
  echo "Catalog request HTTP status: $SYNC_HTTP (expected 200)" >&2
  exit 1
fi
python - "$BASE/wvp-catalog-sync-response.json" <<'PY'
import json,sys
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
if not isinstance(r,dict) or r.get('code',0)!=0:
    raise SystemExit('Upstream WVP declined Catalog request (business code '+str(r.get('code'))+')')
print('WVP accepted an explicit Catalog refresh request; checking actual MySQL next.')
PY
echo "::endgroup::"

echo "::group::Verify real WVP DB online flag AND Catalog channel"
passed=0
VALUE="no_database_response"
for n in $(seq 1 55); do
  if [ "$(docker inspect -f '{{.State.Running}}' "$SIM_CONTAINER" 2>/dev/null || true)" != "true" ]; then
    echo "GB SIP simulator container exited before registration" >&2
    docker logs "$SIM_CONTAINER" > "$BASE/simulator.log" 2>&1 || true
    exit 1
  fi
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
  docker logs "$SIM_CONTAINER" > "$BASE/simulator.log" 2>&1 || true
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
        'catalog_mentions':len(re.findall('Catalog|目录',data,re.I)),
        'unknown_message_mentions':len(re.findall('unknow msg',data,re.I)),
        'udp_parse_failures':len(re.findall('parse msg failed',data,re.I)),
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
