# 持续采集 MVP：状态、唯一性、断流重试（2026-10-13）

## 已做和未做

本模块 `app/continuous_capture.py` 复用现有
`app.main._process_source_impl(source_id, max_frames=90, sample_every=10)`
真实 OpenCV 解码、人脸框推理、SQLite 入库的公共内部路径。后台线程连续处理有界批次，
成功批次之间**不重开** RTSP 视频连接；仅发生读取/打开故障时才释放并重新建立，
不引入虚构识别结果或人脸身份去重。

本版**已实现（待 CI/现场验证）**：

1. 管理员对 RTSP/摄像头 source ID 显式启停持续采集；文件源不支持。
2. SQLite 持久保存启停意图，在服务启动时恢复希望运行的任务。
3. 同一个 source ID 仅能有一个持续采集 worker；SQLite 原子租约、定期心跳
   为多应用进程提供互斥保护。对于来自相同 GB28181 通道绑定的多个 live
   source，还通过唯一索引限制只能有一个 `desired_enabled=1` 的采集任务。
4. 视频打不开、无法解码或推理异常时，记录**脱敏错误码**，采用指数退避
   （2～60 秒）重新打开相同视频源 URI。成功则重置连续失败数。
5. 状态包括 pending/running/retrying/stopping/stopped、成功批次、新入库
   抽样帧数、失败次数、最近成功时间。关闭 GB 会话时自动取消对应任务的
   后续批次。
6. 接口使用现有 Bearer Token 鉴权：只有最高权限管理员可启停，
   较低角色只能读取自己辖区内的任务列表。

本版**尚未实现**：

- WVP 侧丢失 live session 后自动重新发起 GB28181 INVITE 并关联新的 source。
  仅反复连接原 RTSP URI，无法保证 WVP 流号改变后的恢复。
- OpenCV `VideoCapture.read()` 在底层阻塞时的硬超时和强制终止；停止任务
  是按批次协作退出，故障卡在底层 API 时不保证立即停止。
- 跨节点高可用、全量自动发现新设备、摄像机在线心跳探测和硬件故障告警。
- 真实长时运行、断网/重注册/流号变化的端到端验收；不能把本页的
  单元测试当成真实 GB28181 稳定性认证。

## 审计补充：2026-10-13

- 正常持续采集使用同一个 OpenCV `VideoCapture` 处理多批次，不再每90帧断流重开。
- 恢复同一源 RTSP 连接时，已落盘采样帧的 `frame_index` 逻辑序号保持单调，
  不将解码器新连接的首帧反复标成源序号 1。该序号是本服务逻辑采集位置，
  不能用作摄像机原生可信拍摄时间戳。
- 持续采集期间，若视频源 URI、类型或场所绑定被后台修改，已有解码器不能继续向
  新场所写入旧流帧；报告脱敏 `SOURCE_RECONFIGURED` 并尝试新连接。
- 持续采集任务停止并立刻再次启动时，若旧线程仍在退出，退出清理时将核对
  持久化 desired_enabled，避免重启意图丢失。
- 新增国标端到端脚本不仅直接测试持久解码两批真实帧，还调用
  `POST /api/capture/jobs/{source_id}/start`，检查实际后台工作者成功批次、
  新入库帧、主动停止和状态最终释放；此项须以最新 CI 运行结果为准。
- **历史成功证据**：PR GB CI #38334709407 的真实国标 SIP/RTP/PS→OpenCV→SQLite
  曾成功（覆盖持久解码器两批次）；后续额外加入的后台任务 API 端到端验收
  不能用该历史运行冒充已通过。

## 操作顺序

1. 在 `/monitor` 的 GB28181 区域实时点播（`POST
   /api/gb28181/bindings/{binding_id}/live`），记录返回的 source_id。
2. 管理员调用 `POST /api/capture/jobs/{source_id}/start`；或点击对应
   国标实时会话的“持续采集”。
3. 用 `GET /api/capture/jobs` 查看任务（仅当前账号辖区）。
4. 用 `POST /api/capture/jobs/{source_id}/stop` 关闭持续采集；
   如果还需要关闭上游流，再调用
   `POST /api/gb28181/sessions/{session_id}/stop`。

启用前必须保证摄像头/RTSP 可达、WVP 配置正确、媒体服务已运行，
`GB28181_RTSP_HOSTS` 严格匹配允许的地址。源 URL 不应含凭证；
任务状态、日志和审计不写入真实人脸照片或视频。

## 验收门槛

- 首先要求 `pytest -q tests/test_continuous_capture.py` 和全量单元测试通过。
  专项测试应覆盖真实 MP4 持续两个批次只打开一次 decoder、帧索引持续递增、
  以及公开 API 无法以 `worker_mode=true` 绕过重复任务检查。
  既有 GB28181 真实视频 E2E CI 仍独立保留，不能用模拟 worker 测试替代。
- 再使用**实际 GB28181 → WVP → RTSP 的含人脸视频流**持续采集不少于两个批次，确认 SQLite samples
  和 face_boxes 产生新记录；计数继续标示为“抽样帧/检测框/窗口”，不是人数。
- 人工重复点“开始”，并在相同通道创建第二个 live source 后尝试并行启动，
  预期得到单 worker 和 HTTP 409，不出现并发重复记录。
- 中断 RTSP 约 30 秒，观察 `retrying` 与脱敏错误码，再恢复原 URI，
  观察 `last_success_at` 更新、`failure_count` 清零。
- 重启服务，确保启用意图仍存在且旧租约过期后能恢复；关闭 session 后停止。
- 新增的 WVP 重新 INVITE 恢复、OpenCV 硬超时应在下一阶段分别验证，
  未通过前不声称“国标自动恢复”完成。

## 部署限制

目前是面向单台演示服务器的线程方案。使用多个 Uvicorn worker 时 SQLite
租约能避免常规并行处理，但在进程突然崩溃后最多存在 120 秒旧租约恢复
等待；跨主机共享 DB 与强制终止阻塞解码并未支持。适宜单实例
`uvicorn app.main:app --workers 1` 的演示部署。


## 实际 Demo 入口与浏览器验收（2026-10-13）

为避免必须先准备 GB28181 摄像头才能体验“连续采集任务”界面，`/monitor` 的
“持续采集任务”区现在直接列出**当前管理员辖区内已启用的 RTSP 视频源**，由
`GET /api/sources` 动态取得；管理员选择源后点击“开始持续采集”即可启动
现有后台 worker，状态表提供“停止任务”。已有国标实时点播会话里的“持续采集”
入口继续保留。**没有安装模型、没有复制协议实现、没有另建业务数据库或 workflow。**

演示次序：
1. 在 `/admin` 登记已获授权的真实 RTSP 地址并绑定场所（没有视频源时不会显示假设备）。
2. 用州级管理员登录，进入 `/monitor`，选择现有 RTSP 源，点击“开始持续采集”。
3. 查看 `pending/running/retrying`、成功批次、新入库帧、错误码；点击“停止任务”。
4. 如暂时没有可用 RTSP，可通过已经存在的真实本地视频解码测试以及**独立合成**
   P099 底库流程完成 Excel 需求 1/3 的分栏演示；不能把离线源的正常失败当作
   成功解码，也不得将匿名检测框当作独立人员。

**已执行的实际验证：**
[GitHub Actions #38365580593](https://github.com/yituanxing/test3/actions/runs/38365580593)
在 PR #3 分支跑通 **107 passed、1 skipped** 的 Python 回归，之后启动真实
Chromium，真实浏览器点击：登录→合成底库100帧归并/版本迁移→监测页从已登记但
刻意离线的 RTSP 源选择并启停任务→匿名视频脸框人工审核→退出。
浏览器验收 **13项检查全部通过、无 JS 页面错误**。工件
[浏览器操作截图+审计 JSON](https://github.com/yituanxing/test3/actions/runs/38365580593/artifacts/11796706584)。
离线 RTSP 只验证“按钮/API/真实任务状态”，**没有任何实际视频帧入库**；
实际国标 SIP/RTP/PS 的 PR 级端到端验收仍是另一道门槛，不能由这次浏览器成功替代。

**当前仍在 Draft PR #3，main 尚未合入。** 不新增 SAM3、大视觉模型或跨摄像头
身份聚合。闭环中合成人员底库与真实匿名帧严格隔离。
