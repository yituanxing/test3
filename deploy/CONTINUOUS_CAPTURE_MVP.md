# 持续采集 MVP：状态、唯一性、断流重试（2026-10-13）

## 已做和未做

本模块 `app/continuous_capture.py` 复用现有
`app.main.process_source(source_id, max_frames=90, sample_every=10)`
真实 OpenCV 解码、人脸框推理、SQLite 入库的路径。后台线程连续处理有界批次，
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
  既有 GB28181 真实视频 E2E CI 仍独立保留，不能用模拟 worker 测试替代。
- 再使用**实际含人脸视频流**持续采集不少于多批次，确认 SQLite samples
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
