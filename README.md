# ZD 真实视频与匿名事件工作台

## 当前实际运行入口（2026-10-14：仅真实数据）

**普通运行已默认关闭合成业务。** 登录后进入 `/real`；`/` 和
`/portal` 均转到 `/real`。虚构人员底库、模拟七类预警、模拟地理轨迹、
旧动漫实验页面及相应写入 API 返回 HTTP 410，不会再生成 P001/P002 或
虚构到访。历史合成代码仅保留给隔离的 pytest 回归测试，不参与正常进程。

本机启动（在仓库根目录执行）：

```bash
bash scripts/start_local_demo.sh
```

首次启动在终端设置管理员密码；然后访问
`http://127.0.0.1:8001/login`。数据库默认位于
`data/real-only/demo.sqlite3`，不复用旧版包含模拟人员的数据文件。
未连接实际摄像机、或没有读取到真实人体事件时，`/real` 就显示 **0**；
这是正确状态，不会用测试样本填充。

**真实视频主线：** 在 `/admin` 配置获授权的真实场所、文件/RTSP 视频源；
在 `/monitor` 检查解码和持续采集状态；使用实际 Frigate/MediaMTX
源进行人体目标检测，服务端用 `/api/frigate/sync` 从 Frigate 同步到
SQLite；最终在 `/real` 查看摄像机、UTC 时间、Frigate 事件 ID、
场所绑定和匿名目标事件数。研究视频在 `/cctv` 单独展示，
**不能当作现场实况，也不能直接认作同一自然人**。

**真实验收：** `cctv-real` 标签下 PR 的 `real-media` CI 使用许可公开
行人录像，经 FFmpeg → MediaMTX RTSP → Frigate → SQLite →
`/api/real/evidence` 验证上游真实目标 ID 的完整传递。CI 的公开视频
**不是用户现场摄像机**，也没有证明 GB28181 实机 SIP/RTP 成功。
未具备可核查跨机位身份真值时，不生成真人跨场所轨迹或身份级预警。

## EPFL 真实双机视频 → 人员活动（限定 Demo）

本 Demo **无需安装两台实体摄像机，也不需要再找其他监控素材**。
复用已有真实 EPFL 六人实验室同步双机视频（官方 `6p-c0.avi` 和
`6p-c1.avi`），已验的 YOLO11n + 共同地面标定 + Hungarian 匹配。
视频及官方六人 ID 仅用于 CI 研究验证，不进入公开源码包、正常运行身份库。

已接入主系统：`/real` → **真实双摄像机 · 匿名人员活动**。
点击导入后使用同一次 owner CI 从原始 AVI 实际推理、生成并打包的
`config/epfl6p_real_pair_candidates_v1.json`，将每对原始帧号、两个机位模型框、
坐标、距离、原片 SHA 与 FPS 保存进 SQLite，由 `GET /api/real/activity`
计算录像内匿名活动片段 V001… 和视频时间线，人工复核否定的配对不计入活动。
没有该真实视频分析包，页面显示无数据而不是生成 P### 记录。

跨时刻片段还必须通过独立的官方六人标注回归检查：匹配与关联过程不读六人编号，验证阶段才检查是否发生跨真人错误合并；若发现混合 ID，真实双机 owner CI 直接失败。

原来 119 次双机匹配是**41 个视频时刻的 119 次配对**，并不是 119 位真人。
跨时刻匿名活动片段只有经过独立六人真值评分才启用；如果发现身份错误合并，就自动降级到真实的**单帧双机配对活动**。这样的片段是同一录像中的
**可追溯轨迹片段而非永久身份**；不生成跨不同场所的到访、独立人数、
人脸身份或七类虚构预警。只有这两台同步摄像机属于同一个实际场景，
所以本 Demo 不扩展到任意监控之间的匹配。

下载带真实双机研究证据的演示包，应使用带 `cctv-real` 标签并实际执行
“Evaluate EPFL six-person official GT” 的 CI `zd-p0-excel-demo-source` 产物；
普通纯源码目录不附带录制视频产生的结果，不能假称人员活动已经出现。

### 以下为历史原型记录（不再是当前操作入口）

早期版本有独立的合成规则演示；下方文档和原 Excel 进度保留作需求追踪，
其中的 `/portal`、`/base`、`/business` 等路径在当前真实模式中已禁用。


## 最终演示与页面验收（2026-10-14）

登录后从 `/portal?tab=acceptance` 开始，按「真实匿名人体关联 → 合成人员底库 → 七类合成规则」顺序走读。Excel 23项真实状态仍是3项已验管理、10项合成、8项部分、2项缺失。详细可执行步骤和许可边界参见 [DEMO_FINAL_WALKTHROUGH_20261014.md](DEMO_FINAL_WALKTHROUGH_20261014.md)。界面支持状态/需求筛选、折叠详情、跨页匿名候选审核；这不是23项真实功能全部实现。

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

## 持续采集与验收边界

持续采集的启停、去重防护、SQLite 状态与自动退避见
[持续采集 MVP 说明](deploy/CONTINUOUS_CAPTURE_MVP.md)。正常工作时保持同一个
RTSP 解码器跨批次读取，只有失败才重建连接；支持场所/视频源配置变化隔离。
本次 PR 必须通过真实 WVP/SIP/RTP/PS 媒体流的**后台工作者 API 两批次**验收，
不能只把单次视频处理通过作为持续采集验收。

## GB28181 国标视频接入（WVP-PRO + ZLMediaKit，非自行实现SIP）

现有 `/monitor` 页面已增加设备/通道查询、绑定辖区场所、实时点播、历史录像回放和 RTSP 实测探测。由 [648540858/wvp-GB28181-pro](https://github.com/648540858/wvp-GB28181-pro) 执行 SIP 注册、目录、INVITE/Playback，由 [ZLMediaKit](https://github.com/ZLMediaKit/ZLMediaKit) 收 RTP/PS 并转成 RTSP；test3 只做受控 WVP API 适配和现有匿名视频分析，**不创建虚构摄像头或真实人员身份**。

独立部署 WVP/ZLMediaKit 与安全配置、环境变量、实际需要的设备验收步骤，请阅读 [deploy/GB28181_WVP_GUIDE.md](deploy/GB28181_WVP_GUIDE.md)。需要设置 `GB28181_WVP_URL`、`GB28181_WVP_API_KEY` 和 `GB28181_RTSP_HOSTS`；API Key 不应进入公开仓库。设备注册成功、国标 RTP PS 收流、历史录像回放均须通过真实授权设备联调才能标注完成。

CI 另验证 WVP HTTP 契约与正确角色权限，并验证“WVP API 适配 → 正在直播的真实 RTSP → OpenCV → SQLite”数据链路；其中 **WVP SIP 控制面在 CI 中由本地 HTTP 测试服务替代，并不证明 GB28181 REGISTER/INVITE 实机成功**。
