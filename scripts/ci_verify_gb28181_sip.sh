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
# Use SDP transport matching the actual offer, not the legacy TCP-only reply.
invite=root/'internal/invite/invite.go'
content=invite.read_text()
old='Proto: "TCP/RTP/AVP",'
new='Proto: func() string { if inv.remote.proto == "TCP" { return "TCP/RTP/AVP" }; return "RTP/AVP" }(),'
if content.count(old)!=1:raise SystemExit('Upstream invite version changed')
content=content.replace(old,new)
# FFmpeg's generic MPEG-PS muxer emits PES stream-id E2 without a
# GB28181 H.264 program-stream-map. Use the upstream simulator's own
# GB packetizer (Send2data) on Annex-B H264 access units instead.
start=content.index('func (inv *Invite) sendFile(')
end=content.index('func isPsHead(', start)
replacement='''func (inv *Invite) sendFile(buf []byte) {
    delimiter := []byte{0, 0, 0, 1, 9} // Annex-B H264 AUD, one per access unit
    if len(buf) < len(delimiter) || !bytes.Equal(buf[:len(delimiter)], delimiter) {
        return
    }
    for len(buf) > 0 {
        if atomic.LoadInt32(&inv.state) == idle { return }
        n := len(buf)
        if len(buf) > len(delimiter) {
            next := bytes.Index(buf[len(delimiter):], delimiter)
            if next >= 0 { n = len(delimiter) + next }
        }
        if n <= 0 { return }
        frame := buf[:n]
        // Emit PS pack + PSM(H264=0x1B, video PES=0xE0) on IDR access units.
        isIDR := bytes.Contains(frame, []byte{0,0,0,1,0x65})
        inv.rtp.Send2data(frame, isIDR, inv.pts)
        inv.pts += 6000 // RTP clock 90 kHz / 15 fps
        if inv.pts == 6000 { println("CI_GB_PS_RTP_FIRST_AU_SENT") }
        time.Sleep(time.Millisecond * 67)
        buf = buf[n:]
    }
}
'''
content=content[:start]+replacement+content[end:]
imp='import (\\n\\t"io/ioutil"'
assert content.count(imp)==1
content=content.replace(imp,'import (\\n\\t"bytes"\\n\\t"io/ioutil"')
old='\tbyed   chan bool'
assert content.count(old)==1
content=content.replace(old,old+'\\n\\tpts    uint64')
invite.write_text(content)

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
RecordSip=true
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
  up -d polaris-mysql polaris-redis polaris-media
MEDIA_CID="$(docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml" ps -q polaris-media)"
MEDIA_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$MEDIA_CID")"
test -n "$MEDIA_IP"
sed -i "s/^SDP_IP=127.0.0.1$/SDP_IP=$MEDIA_IP/" "$BASE/wvp/docker/.env"
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
# Deterministic, license-free moving frames, encoded H.264 in MPEG-PS
# for the same simulator to send over genuine GB28181 RTP.
if ! command -v ffmpeg >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq ffmpeg
fi
ffmpeg -hide_banner -loglevel error -nostdin -y -f lavfi \
  -i 'testsrc2=size=320x240:rate=15' -t 45 -an \
  -c:v libx264 -pix_fmt yuv420p -g 15 \
  -x264-params 'aud=1:repeat-headers=1:scenecut=0' \
  -f h264 "$BASE/sim/test.dat"
test -s "$BASE/sim/test.dat"
docker run -d --name "$SIM_CONTAINER" \
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
  # Early samples are essential: upstream WVP may expire sync progress
  # before the 165-second DB assertion timeout.
  if [ "$n" = 1 ] || [ "$n" = 5 ] || [ "$n" = 15 ] || [ "$n" = 35 ]; then
    curl -sS --max-time 4       "http://127.0.0.1:18978/api/device/query/sync_status?deviceId=$DEVICE"       -o "$BASE/wvp-sync-$n.json" 2>/dev/null || true
    python - "$BASE/wvp-sync-$n.json" "$n" <<'PY'
import json,sys
try:
    v=json.load(open(sys.argv[1],encoding='utf-8'))
    d=v.get('data') if isinstance(v,dict) else None
    d=d if isinstance(d,dict) else {}
    print('WVP Catalog progress sample:',json.dumps({
        'poll':int(sys.argv[2]),'api_code':v.get('code'),
        'total':d.get('total'),'current':d.get('current'),
        'running':d.get('syncIng'),'has_error':bool(d.get('errorMsg')),
    },ensure_ascii=False))
except (OSError,ValueError,AttributeError):
    print('WVP Catalog progress sample: unavailable')
PY
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$SIM_CONTAINER" 2>/dev/null || true)" != "true" ]; then
    echo "GB SIP simulator container exited before registration" >&2
    docker logs "$SIM_CONTAINER" > "$BASE/simulator.log" 2>&1 || true
    exit 1
  fi
  # One SQL truth query checks both the online REGISTER state and the catalog
  # channel. We do not rely on a SIP ACK generated by test mocks.
  # WVP schema: channel.device_id is the channel GB ID; channel.data_device_id
  # references parent wvp_device.id (numeric PK). gb_device_id is a separate
  # optional platform-sharing override and is not the physical channel key.
  SQL="SELECT CONCAT(COUNT(DISTINCT d.device_id), ':', COUNT(DISTINCT c.id)) FROM wvp_device d LEFT JOIN wvp_device_channel c ON c.data_device_id=d.id AND c.device_id='$CHANNEL' WHERE d.device_id='$DEVICE' AND d.on_line=1;"
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
  SQL_STATE="SELECT CONCAT(COALESCE((SELECT COUNT(*) FROM wvp_device WHERE device_id='$DEVICE'),0),':',COALESCE((SELECT MAX(on_line) FROM wvp_device WHERE device_id='$DEVICE'),-1),':',COALESCE((SELECT COUNT(*) FROM wvp_device_channel c JOIN wvp_device d ON c.data_device_id=d.id WHERE d.device_id='$DEVICE'),0),':',COALESCE((SELECT COUNT(*) FROM wvp_device_channel c JOIN wvp_device d ON c.data_device_id=d.id WHERE d.device_id='$DEVICE' AND c.device_id='$CHANNEL'),0));"
  DB_STATE="$(docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml" exec -T polaris-mysql mysql -uwvp_user -pwvp_password -N wvp -e "$SQL_STATE" 2>/dev/null || true)"
  export DB_STATE
  echo "WVP persisted SIP state (device rows:online flag:channel rows:matching channel) = ${DB_STATE:-unavailable}" >&2
  docker logs "$SIM_CONTAINER" > "$BASE/simulator.log" 2>&1 || true
  docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml"     logs --no-color polaris-wvp > "$BASE/wvp-runtime.log" 2>&1 || true
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
runtime=base/'wvp-runtime.log'
if runtime.exists():
    data=runtime.read_text(encoding='utf-8',errors='replace')
    v['wvp_runtime_signal_counts']={
        'channel_received':len(re.findall('收到通道',data)),
        'channel_sync':len(re.findall('同步通道',data)),
        'xml_parse_error':len(re.findall('xml解析.*失败|解析XML消息内容异常',data,re.I)),
        'unknown_device':len(re.findall('设备未找到|device .* not found',data,re.I)),
        'channel_decode_error':len(re.findall('目录订阅.*解析失败|收到通道.*异常',data)),
        'channel_write_error':len(re.findall('直接入库失败|同步通道.*失败',data)),
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
  # Snapshot WVP's own asynchronous Catalog state: HTTP /sync returned
  # "started", not "finished". Print only safe status counters.
  SYNC_STATUS="$(curl -sS --max-time 5 "http://127.0.0.1:18978/api/device/query/sync_status?deviceId=$DEVICE" || true)"
  SYNC_STATUS_JSON="$SYNC_STATUS" python - <<'PY'
import json,os
try:
    d=json.loads(os.environ.get('SYNC_STATUS_JSON',''))
    state=d.get('data') if isinstance(d,dict) else None
    state=state if isinstance(state,dict) else {}
    print('WVP Catalog async state:',json.dumps({
        'api_code':d.get('code'),'total':state.get('total'),
        'current':state.get('current'),'running':state.get('syncIng'),
        'has_error':bool(state.get('errorMsg'))
    },ensure_ascii=False))
except (TypeError,ValueError,AttributeError):
    print('WVP Catalog async state: unavailable')
PY
  echo "GB SIP REGISTER or Catalog not proven; WVP database requirements failed" >&2
  exit 1
fi
echo "SUCCESS: WVP persisted 1 online SIP device and its advertised Catalog channel."
echo "::endgroup::"

# Persist control-plane proof independently of subsequent RTP media outcome.
python - <<'PY'
import json,os
from pathlib import Path
p=Path(os.environ['REPORT'])
v=json.loads(p.read_text(encoding='utf-8'))
v.update({'live_sip_registration':True,'wvp_persisted_online_device':True,
          'catalog_channel_received':True,'sip_catalog_verdict':'PASS'})
p.write_text(json.dumps(v,indent=2,ensure_ascii=False),encoding='utf-8')
PY

echo "::group::Real WVP live INVITE and media endpoint"
PLAY_HTTP="$(curl -sS --max-time 45 -o "$BASE/wvp-play.json" \
  -w '%{http_code}' "http://127.0.0.1:18978/api/play/start/$DEVICE/$CHANNEL" || true)"
if [ "$PLAY_HTTP" != 200 ]; then
  echo "WVP INVITE play request returned HTTP $PLAY_HTTP" >&2
  exit 1
fi
python - "$BASE/wvp-play.json" "$BASE/gb-rtsp.url" <<'PY'
import json,sys
from pathlib import Path
from urllib.parse import urlsplit
response=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
data=response.get('data') or {}
uri=data.get('rtsp') if isinstance(data,dict) else None
if response.get('code')!=0 or not isinstance(uri,str):
    raise SystemExit('WVP did not complete real SIP INVITE/stream activation')
parsed=urlsplit(uri)
if parsed.scheme!='rtsp' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.password:
    raise SystemExit('WVP returned an invalid/nonlocal RTSP endpoint')
Path(sys.argv[2]).write_text(uri,encoding='utf-8')
print('WVP reported a local RTSP endpoint after real SIP INVITE')
PY
echo "::endgroup::"

echo "::group::Decode real GB RTP/PS video through ZLMediaKit RTSP"
RTSP_URL="$(cat "$BASE/gb-rtsp.url")"
decoded=0
for attempt in 1 2 3 4; do
  if timeout 18 ffmpeg -hide_banner -loglevel error -nostdin -y \
       -rtsp_transport tcp -rw_timeout 8000000 -i "$RTSP_URL" \
       -frames:v 1 "$BASE/gb-decoded.jpg" >"$BASE/gb-ffmpeg.log" 2>&1 \
       && test -s "$BASE/gb-decoded.jpg"; then
    decoded=1
    break
  fi
  sleep 2
done
if [ "$decoded" != 1 ]; then
  python - "$BASE/gb-ffmpeg.log" "$BASE/gb-rtsp.url" <<'PY'
import sys,re,socket
from pathlib import Path
from urllib.parse import urlsplit
s=Path(sys.argv[1]).read_text(encoding='utf-8',errors='replace')
u=urlsplit(Path(sys.argv[2]).read_text(encoding='utf-8'))
errors={
    'connection_refused':r'connection refused',
    'rtsp_not_found':r'404 not found',
    'rtsp_unauthorized':r'401 unauthorized|403 forbidden',
    'invalid_media':r'invalid data|could not find codec|unspecified size',
    'timeout':r'timed out|timeout',
    'no_streams':r'no video|does not contain any stream',
    'protocol_error':r'rtsp.*error|error.*rtsp',
}
port=u.port or 554
try:
    with socket.create_connection(('127.0.0.1',port),timeout=2):
        reachable=True
except OSError:
    reachable=False
print('GB media RTSP diagnostic:',{'port':port,'tcp_reachable':reachable,
   'ffmpeg_failures':{k:len(re.findall(p,s,re.I)) for k,p in errors.items()}})
PY
  docker logs "$SIM_CONTAINER" > "$BASE/gb-device-runtime.log" 2>&1 || true
  docker compose -p "$PROJECT" -f "$BASE/wvp/docker/docker-compose.yml" \
    logs --no-color polaris-media > "$BASE/gb-media-runtime.log" 2>&1 || true
  python - "$BASE" <<'PY'
import json,re,sys
from pathlib import Path
p=Path(sys.argv[1])
s=(p/'gb-device-runtime.log').read_text(encoding='utf-8',errors='replace') if (p/'gb-device-runtime.log').exists() else ''
z=(p/'gb-media-runtime.log').read_text(encoding='utf-8',errors='replace') if (p/'gb-media-runtime.log').exists() else ''
print('GB simulator media signals:',{
 'invite_received':len(re.findall('Sever.*Invite',s,re.I)),
 'ack_received':len(re.findall('Server.*ACK',s,re.I)),
 'rtp_sender_started':len(re.findall('stream data will be write',s,re.I)),
 'rtp_connect_failed':len(re.findall('connect failed|dial tcp|write data by udp error',s,re.I)),
 'panic':len(re.findall('panic:|runtime error:',s,re.I)),
 'zlm_media_registered':len(re.findall('regist|on_stream_changed|stream registered',z,re.I)),
 'zlm_rtp_errors':len(re.findall('Rtp.*error|invalid.*RTP|recv RTP.*failed',z,re.I))
})
PY
  echo "GB PS/RTP media decode not proven; Catalog remains independently PASS." >&2
  exit 1
fi
python - "$BASE/gb-decoded.jpg" <<'PY'
from pathlib import Path
import sys
b=Path(sys.argv[1]).read_bytes()
if len(b)<1000 or not b.startswith(bytes.fromhex('ffd8ff')):
    raise SystemExit('GB media frame is not a valid nonempty JPEG')
print('Decoded GB RTP/PS moving-video frame, JPEG bytes:',len(b))
PY
cp "$BASE/gb-decoded.jpg" reports/gb28181-decoded-ci.jpg
echo "::endgroup::"

python - <<'PY'
import json,os
from datetime import datetime,timezone
from pathlib import Path
path=Path(os.environ["REPORT"])
v=json.loads(path.read_text(encoding="utf-8"))
v.update({"verdict":"PASS","sip_invite_rtp_ps_video":"PASS","live_sip_registration":True,
          "wvp_persisted_online_device":True,"catalog_channel_received":True,
          "check_time_utc":datetime.now(timezone.utc).isoformat(),
          "evidence":"WVP live MySQL wvp_device.on_line=1 AND wvp_device_channel.data_device_id=parent.id AND channel.device_id matches; independent upstream GB SIP simulator running",
          "note":"Real SIP REGISTER/Catalog/INVITE, PS/RTP to ZLM and an actual decoded frame; historical playback not tested."})
path.write_text(json.dumps(v,indent=2,ensure_ascii=False),encoding="utf-8")
print("Real upstream GB28181 SIP verdict:",v["verdict"])
PY
