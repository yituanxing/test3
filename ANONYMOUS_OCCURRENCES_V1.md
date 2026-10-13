# 匿名出现事件公共契约 V1（2026-10-13）

## 为什么要补这一层

Excel #3 底库、#4–12 业务、#13–19 检索/关系依赖“一个有效的人物到访”；
视频层实际拿到的却是**一个摄像机里的匿名轨迹或多个视频帧**。
不能从人体框数量直接计算独立人数：同一人可出现 100 帧；两机位同号轨迹可能是不同人。
原有 P### 是纯合成已知身份，不能因增加真实视频而自动变成真人身份。

### 本轮已经落地

- 新增 `app/occurrences.py`，与已验证的 `app/site_model.py` 共用现有 SQLite 表和辖区权限，**不复制人物底库引擎、不增加新的人员身份表**。
- `GET /api/site/occurrences`：把 **Frigate** `(camera,event_id)` 与 CAVIAR `(source_id, video_sha256, camera_local_track_id)` 转换为统一、带证据的局部 occurrence 契约。局部 occurrence_key 由 `SHA256(provider,scope,local_track_id)` 稳定计算。
- 对 100 帧人工框中的同一个 camera-local ID，输出**一个局部 occurrence**，同时保留 `annotation_frames=100` 和 `first_seen/last_seen` 帧区间。两路摄像机即使恰好都是 local-1，也不会被默认归并为一个真实人。
- `GET /api/site/occurrences/summary`：按辖区内**场所→机位→来源**汇总局部 occurrence 数量与 CAVIAR 视频人工框数量；Frigate 和 CAVIAR 分开显示，且明确两者使用不同的时间基准：
  - Frigate：`time_basis=UTC_event_clock`，基于真实事件时间戳；
  - CAVIAR：`time_basis=archival_video_frame_number`，只有视频帧序号、没有今天的日历时间。
  - 无论哪个来源，`subject_id=null`、`counts_as_unique_person=false`、`counts_as_visit=false`。
- `/cctv` 原真人固定视频页增加“公共匿名出现事件”区域，允许查看每机位局部目标和逐帧标注，不混入已有 P### 模拟业务。
- `tests/test_site_model.py` 新用例包含 100 帧 1 条局部轨迹×2 个 CAVIAR 机位、相同 local-1 的 2 条 Frigate 事件共4条独立 occurrence、作用域隔离、idempotent key、真实/合成数据隔离、UTC与历史帧时钟不能混加，Chromium 使用实际页面检查无视频时不伪造事件。

### 关键边界及下一层

1. 这不是识别同一个人：即使 occurrence_key 稳定，它依然仅在“provider+camera/source+视频内容修订+localID”的命名空间内稳定。
2. 这不是到访：没有**可信主体链接**与可靠时间时钟就无法推断两个机位轨迹属于同一个人或是否访问过场所B。不能把本机 target track 当成真实人员数据库。
3. 不能把真实媒体与已知 P### 合成事件隐式 JOIN；合成底库版 `base_registry` 和七类 `business.analyze` 保留原样。
4. 研究标注是人工真值，不是本系统检测模型预测。后续人体检测应与这份人工 GT 对照测试 precision/recall，模型输出作为候选匿名 occurrence，再决定是否进入复核流程。
5. 当前 occurrence API 为 **Demo 有界只读投影（单次最多200条）**；大规模生产需服务端索引分页、历史窗口聚合及独立事件快照，一切以官方验收真值为前提。

## 下一步优先级

- 优先把“匿名局部事件→可信事件复核→数据质量”打通，不做人脸身份推断。
- 另行验证模型检测和单摄像机目标跟踪（GT 精确率、误检率、连续性）。
- 跨机位、跨场所人物身份关联应是独立受控组件；不能从 CAVIAR 2 个同走廊视角的局部 ID 假设真实唯一身份。
- Excel 有关场所/摄像机/规则/底库的原公共能力只需接受**证据明确的后续上游事件**，不要按页面重新复制。

## 实现位置
`app/occurrences.py`、`app/site_model.py`、`static/cctv.html`、`tests/test_site_model.py`、`scripts/browser_smoke.py`。
