# ZD 场所分析演示系统

本仓库提供两套语义隔离的可运行服务：

1. **业务Web平台**：`python -m uvicorn app.main:app --port 8001`。页面：`/admin`、`/business`、`/monitor`；行政区域、场所与设备管理、视频观测、合成规则、审核和纠错。
2. **Frigate技术验收桥**：`python -m uvicorn app.bridge:app --port 8000`，供 Docker/RTSP/OpenVINO 实机验收使用。

## 最终业务演示入口（V1.3）

- `/login`：登录后进入 `/portal` 综合态势门户；包含授权辖区场所列表、场所真实匿名检测统计、合成预警中心、规则证据详情和州级复核。
- `/portal?tab=places`：场所列表；`/portal?tab=alerts`：模拟规则候选；与原有管理工作台互相跳转。
- 首次启动需在本机通过 `python -m scripts.bootstrap_roles --username demo_state --region-id 1` 创建州级演示用户，并输入至少12位密码。区域 ID 以 SQLite 实际数据为准。
- 视频生产链路仍需 Frigate/MediaMTX。主 Web 真实事件闭环由最新 `scripts/verify_portal_live.py` + GitHub Actions 验证。
- 真实匿名人形目标事件与合成 P### 身份、到访、预警严格分离；不得宣称已经实现真实人员身份识别。

## V1.4 本地演示交付

Linux/macOS: 在仓库根目录执行 `bash scripts/start_local_demo.sh`。首次运行会创建 .venv、安装 Python 依赖并交互设置管理员 `demo_state` 的密码。启动后访问 `http://127.0.0.1:8001/login`，仅绑定 127.0.0.1，不支持直接公网部署。

GitHub Actions 新增真实 Chromium 登录与导航验证（截图工件 `ui-acceptance`），并生成不含 SQLite、用户会话或凭证的源码包 `zd-local-demo-source`。可参见 `UI_ACCEPTANCE_V1.4.md`。源码包首次运行仍需要互联网安装依赖，且未自带 Frigate 服务。

安装及运行：

```bash
python -m pip install -r requirements.txt -r requirements-business.txt
pytest -q
python -m uvicorn app.main:app --port 8001
```

完整业务操作和限制见 [DEMO_INTEGRATION.md](DEMO_INTEGRATION.md)。测试素材授权与上游依赖见 [THIRD_PARTY.md](THIRD_PARTY.md)。

**检测质量说明：** Frigate 在 [CI #38213089455](https://github.com/yituanxing/test3/actions/runs/38213089455) 上已通过 Intel 公共样例视频→FFmpeg→MediaMTX→RTSP→Frigate/OpenVINO→FastAPI→SQLite 的完整链路，产生2条真实 `person` 目标事件并幂等入库。**2条检测事件不是2个不同的人，不能据此估算查准率、召回率或准确识别人员身份。** 真实人物身份匹配未验证；涉及跨场所身份的业务规则只使用虚构人员。

**数据合规：** 公开仓库只包含许可证允许的演示素材，不存储生产场所录像、真实人员身份、人脸向量、秘密凭证。

## 当前 Demo 验收基线（2026-10-13）

请先阅读 [DELIVERY_SCOPE_20261013.md](DELIVERY_SCOPE_20261013.md)：以原始 23 项 Excel、需求 1/3 和已验证的 CI 为唯一交付基线。实际匿名视频检测与合成 P### 人员底库严格隔离；20 秒连续有脸窗口只是运营统计，**不是**真实人员、有效出现事件或身份聚类。未通过实际 CI 的持续采集开发分支不能标为“已交付”。

## 需求 1 / 3 核心设计与计数口径

参见 [CORE_VIDEO_ROSTER_SPEC_V1.md](CORE_VIDEO_ROSTER_SPEC_V1.md)：区分原始帧检测、连续事件、有效出现、到访会话及底库人员。次数优先依据明确事件分组后的有效出现数；已发布快照不可直接修改，必须生成新草稿后裁决发布。真实视频匿名事件不能冒充已确认人员身份。

## GB28181 国标视频接入（WVP-PRO + ZLMediaKit，非自行实现SIP）

现有 `/monitor` 页面已增加设备/通道查询、绑定辖区场所、实时点播、历史录像回放和 RTSP 实测探测。由 [648540858/wvp-GB28181-pro](https://github.com/648540858/wvp-GB28181-pro) 执行 SIP 注册、目录、INVITE/Playback，由 [ZLMediaKit](https://github.com/ZLMediaKit/ZLMediaKit) 收 RTP/PS 并转成 RTSP；test3 只做受控 WVP API 适配和现有匿名视频分析，**不创建虚构摄像头或真实人员身份**。

独立部署 WVP/ZLMediaKit 与安全配置、环境变量、实际需要的设备验收步骤，请阅读 [deploy/GB28181_WVP_GUIDE.md](deploy/GB28181_WVP_GUIDE.md)。需要设置 `GB28181_WVP_URL`、`GB28181_WVP_API_KEY` 和 `GB28181_RTSP_HOSTS`；API Key 不应进入公开仓库。设备注册成功、国标 RTP PS 收流、历史录像回放均须通过真实授权设备联调才能标注完成。

CI 另验证 WVP HTTP 契约与正确角色权限，并验证“WVP API 适配 → 正在直播的真实 RTSP → OpenCV → SQLite”数据链路；其中 **WVP SIP 控制面在 CI 中由本地 HTTP 测试服务替代，并不证明 GB28181 REGISTER/INVITE 实机成功**。
