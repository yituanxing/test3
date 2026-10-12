# GB28181 接入实施说明：复用 WVP-GB28181-PRO + ZLMediaKit

**状态（2026-10-12）**：现已实现 WVP HTTP API → test3 场所通道绑定 → WVP 实时/历史点播 → 校验 RTSP → test3 `sources` / `gb28181_stream_sessions`。已测适配器协议契约，**尚无真实 GB28181 摄像头/SIP 注册的端到端认证结果**。请勿将此文档或 API 测试绿色误称为“国标完整接入验收”。

## 1. 不重写 GB28181 协议栈

官方上游：
- [648540858/wvp-GB28181-pro](https://github.com/648540858/wvp-GB28181-pro)，MIT，设备 SIP REGISTER、目录、INVITE、实时点播、录像查询/回放；2026-10 仍在维护。源码真实接口：`GET /api/device/query/devices`、`GET /api/device/query/devices/{deviceId}/channels`、`GET /api/play/start/{deviceId}/{channelId}`、`GET /api/playback/start/{deviceId}/{channelId}?startTime=...&endTime=...`、停止接口。
- [ZLMediaKit/ZLMediaKit](https://github.com/ZLMediaKit/ZLMediaKit)，GB28181 RTP/PS 解复用、RTSP/RTMP/HLS 转发。其自有代码为 MIT 类授权，需保留项目标识并检查依赖许可证。

架构：

```text
经授权的GB28181摄像机/NVR
         │ SIP REGISTER / Catalog / INVITE
         ▼
    上游 WVP-PRO          ← 我们不实现 SIP、国标设备注册
         │ 设备发 RTP PS/TS
         ▼
    ZLMediaKit            ← 把 GB RTP 解码/转成 RTSP
         │ RTSP地址
         ▼
   test3 GB28181 API      ← 绑定设备/通道与已管理场所；保护 Key、校验地址
         │ 通过 sources(kind=rtsp)
         ▼
   现有 OpenCV 视频解码及匿名人脸检测
         │
         ▼
  真实匿名观测/人工误检审核（与虚构 P### 身份底库严格分离）
```

## 2. 上游按官方实现安装，不复制源代码到 test3

上游仓库自带 `docker/docker-compose.yml`，含 WVP、ZLM、MySQL、Redis、Nginx。需要联网安装 Docker 或使用 GitHub Runner；命令示例：

```bash
git clone https://github.com/648540858/wvp-GB28181-pro.git vendor-wvp-pro
cd vendor-wvp-pro/docker
# **先人工检查并更换所有上游测试默认值**
# 编辑 .env、docker-compose.yml 及相关配置：
# - MySQL root / wvp_user 密码；Redis 权限
# - SIP_Id、SIP_Domain、SIP_Password、SIP_ShowIP
# - ZLM_SERCERT、SDP_IP、Stream_IP；RTP/SIP 端口和网络发布
# - 切勿把 127.0.0.1 作为外部设备 SDP 地址
# - 后端 WVP API（示例端口 18978）只允许受信网络访问
docker compose up -d --build
```

**注意**：上游示例 Compose 中含公开默认数据库口令及示例密钥，不能原样用于公网。不要把本机设备凭证、视频或敏感信息加入 test3 公开 Git 仓库。WVP 的设备目录/RTSP URL 可能包含私有局域网地址，务必让 test3 后端可以连通且不要将 WVP 管理 API 公网暴露。使用合法授权的测试设备与录像。

WVP 控制面 HTTP 使用 `api-key` 请求头，通过管理员在 WVP 里创建**最小权限** API Key。该 Key 只存环境变量，不能出现在 URL、前端 JS、Git、审计表或 CI 日志。

## 3. test3 配置和操作

```bash
# 在启动 Python Web 进程的机器上配置（以下值仅为本机示例，不是真实凭证）
export GB28181_WVP_URL=http://127.0.0.1:18978
export GB28181_WVP_API_KEY='这里使用上游新建的私密APIKey'
export GB28181_RTSP_HOSTS='127.0.0.1,zlmediakit-host.example'
export GB28181_DEVICE_TZ=Asia/Shanghai
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

在 test3 用州级演示管理员登录，然后：

1. `GET /api/gb28181/status`：只代表已配置 WVP 地址/Key，**不代表设备已在线**。
2. `GET /api/gb28181/devices` 与 `GET /api/gb28181/devices/{deviceId}/channels`：读取 WVP 真实目录（API Key 仅由服务端携带）。
3. `POST /api/gb28181/bindings`：提交 `{"place_id":1,"device_id":"34020000001320000001","channel_id":"34020000001320000002","name":"场所入口"}`，仅绑定元数据，不创建虚构视频或身份。
4. `POST /api/gb28181/bindings/{id}/live`：真实调用 WVP 发起 SIP INVITE。**只有 WVP 返回 code=0 且可信 RTSP 地址后**才新建 test3 的 RTSP 源，返回 `source_id`、`session_id`。当前不会直接冒充已经收到视频帧。
5. `GET /api/sources/{source_id}/probe`：FFprobe 实际探测（需要 WVP/流媒体正在推流）。
6. `POST /api/sources/{source_id}/process?max_frames=80&sample_every=10`：现有 OpenCV 对实际 RTSP 解码并检测；结果入视频观测，不写入虚构身份。
7. 历史视频：`POST /api/gb28181/bindings/{id}/playback` 请求体 `{"start_at":"2026-10-12T00:00:00+08:00","end_at":"2026-10-12T01:00:00+08:00"}`；WVP 以 `GB28181_DEVICE_TZ` 转换到国标设备本地时区并返回该片段的 RTSP。无录像时接口应报错而非造假。
8. `POST /api/gb28181/sessions/{session_id}/stop`：通知 WVP 停流、禁用本次源；已有历史视频观测仍保持原始场所来源。

**安全**：只有一级管理员能查询完整的 GB 设备目录、创建绑定、发起/停止流；下级角色只可查看自己辖区内的绑定与会话元数据。来自 WVP 的 RTSP host 必须位于 `GB28181_RTSP_HOSTS` 白名单；不接受 HTTP 假流、URL 用户名/密码、跨主机流地址。前端和常规状态 API 不回显上游 API Key 或 RTSP URL。

## 4. 真正有效的验收分层

| 测试 | 必须具备的输入/证据 | 结论口径 |
|---|---|---|
| A. WVP 接口适配 | 真实 localhost HTTP 请求 + 状态码/Key/响应 JSON + SQLite/权限 | 仅证明 adapter，**不等于 SIP 国标设备在线** |
| B. ZLMediaKit RTP 收流 | 实际 H264/PS RTP 包进入 ZLM，能从 RTSP 取回有效视频帧 | 仅证明媒体通道，**尚未验证 GB SIP** |
| C. GB28181 实时 | 授权 IPC/NVR 或合规 SIP 设备模拟器注册、目录、INVITE、RTP PS、RTSP 解码、人脸检测 | 满足实时链路的技术验收 |
| D. GB28181 录像回放 | 有已录制历史视频的 GB28181 设备 + 明确起止时间 + 真实流帧 | 满足录像链路验收 |
| E. 场所/权限 | 同一设备多路通道归属、下级用户拒绝跨辖区、会话结束与断线 | 业务整合验收 |

**当前已实现的是 A；C/D 尚待接入合法设备或兼容 GB28181 SIP/RTP 测试设备实际验证。** B 可以单独以 ZLMediaKit 官方 `openRtpServer` + FFmpeg `rtp_mpegts` 验证媒体格式转换，但不能拿 FFmpeg 假装有 GB SIP 设备。应在有设备条件后再填完 C/D，绝不声称 RTSP 代表 GB28181 端到端已经完成。

### 增加的一条真实媒体集成验证

GitHub 的完整视频 CI 在 MediaMTX 和 FFmpeg 正在推送 Intel 许可样片的前提下，通过本地 HTTP 测试服务模拟 *WVP 的控制面 API*，让正式的 `app/gb28181.py` 注册 `rtsp://127.0.0.1:8554/demo`，随后从已登记的 `source_id` **真正解码运动画面，并将匿名观测写入主 Web SQLite**。该阶段的实际帧数、抽帧数、模型检测结果保存在 `reports/verify-wvp-rtsp-bridge.json`。这比纯模拟响应更深入，但仍然**没有验证 GB28181 SIP、实际 PS/RTP 或历史设备录像**。不会把该测试当作 C/D 完成。
