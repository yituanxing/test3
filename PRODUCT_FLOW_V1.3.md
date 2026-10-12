# V1.3 · 综合业务门户（端到端展示入口）

## 产品链路

登录 → 当前授权辖区综合态势 → 场所详情 → 真实匿名检测事件/视频源 → 合成规则候选 → 证据依据 → 人工复核 → 返回态势。

入口：`/login`，登录后跳转 `/portal`。`/portal?tab=places` 查看授权场所，`/portal?place=<ID>` 场所详情，`/portal?tab=alerts` 模拟规则候选列表，`/portal?alert=<key>` 查看合成证据/州级审核。原有六个专业操作台继续保留。

## 数据口径与边界

- **真实**：Frigate/OpenVINO person 事件数量、OpenCV 采样帧、人脸检测框、摄像头/场所绑定；没有真实独立人数/身份/交往关系。
- **合成**：虚构 P### 到访/预警/人员底库。不同来源分栏显示，绝不将真实 person 事件作为 P### 标识。
- 未录入场所经纬度，暂用辖区结构列表，不使用随机坐标绘制误导性地图。
- 场所视频源的「已启用」仅为配置状态，不是假定实时在线；监测记录是最新 100 条事件中的已同步子集。
- 预警中心的列表来自 `/api/insights/overview`，最多展示 30 条模拟规则候选。完整批次管理保留在 `/business`，仅州级演示管理员可执行高级管理操作。

## 已复用的服务端接口

`/api/auth/me`、`/api/places`、`/api/sources`、`/api/insights/overview`、`/api/monitoring/overview`、`/api/frigate/events`、`/api/business/seed`、`/api/business/alerts/{key}/review`。

所有需要授权的接口均沿用 V1.2 服务端鉴权。新门户不引入新账号或持久化层；前端只保存本标签页 Bearer 会话，不回传视频 URI。

## 下一阶段验收

1. 登录不同辖区账号，核验 /portal 的场所、检测事件按辖区限制。
2. 州级初始化虚构场景，检查首页→场所→规则详情→人工核查→状态变化。
3. Frigate 实时流打通后，从 `app.main` 同步真实事件，在场所详情展示。当前 CI 的真实 Frigate 成功用例使用独立 `app.bridge`，不能代替主 Web 端到端验收。
4. 浏览器自动化、单机一键启动、交付说明与打包继续跟进。

## V1.3.1 · 主业务 Web 真实链路认证

`scripts/ci_verify_frigate.sh` 在桥接服务真实 RTSP 验证成功后，额外启动 `app.main` 业务服务，并运行 `python -m scripts.verify_portal_live`：创建一次性的本地州级演示账号、调用真实服务端登录/摄像机绑定/Frigate同步，验证真实匿名事件进入主业务 SQLite、综合门户 /api/insights/overview 的真实视频区块和场所统计，及重复同步无新增。验证源是同一份 Frigate/OpenVINO 实际推理事件；不制造 P### 身份或身份准确率。自动保留 `reports/verify-portal-live.json` 证据。完整 CI 结果以实际 GitHub Actions 为准。
