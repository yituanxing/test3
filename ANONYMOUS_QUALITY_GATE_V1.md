# 匿名目标质量复核与证据失效 V1（2026-10-13）

## 目的

原 Excel #3 人员底库、#6–12 七类规则并不能直接使用每一帧的“人形检测框”。
真实输入必须经过一条明确、独立、可复验的质量门禁。

```
来自 Frigate / CAVIAR 的单机匿名目标
             │
             ▼
       匿名 occurrence_key
             │
             ▼
       视频证据与来源签名
             │
       人工质量复核
      ┌──────┼─────────┐
      │      │         │
合理局部目标  误检    仍需复核
      │      │         │
      └──────┴─────────┘
             │
     视频事件修改了吗？
        │          │
       否          是
        │          ▼
        │       审核过期
        ▼
    只能进入**匿名质量统计**
        │
        └───✘ 不能自动得出真实人物身份、跨机匹配、
                Excel人员主归属、现实“到访”或风险告警
```

**审核“某相机里这个框/目标看起来合理”，不是审核“这是张三”。**
即使人工把一条局部轨迹评定为 valid_local_target，它仍属于一个
具体机位、一个时间基准、一个局部目标编号；不能跨机位拼凑真人身份。

## 本轮已经实际交付

- 新增 `app/occurrence_reviews.py`，`anonymous_occurrence_reviews` 表：
  `occurrence_key`、来源、决策、原因、证据签名、审核者ID、UTC审核时间。
  **仅 INSERT 追加**，保留历史反复人工更正；后台 `admin_audit` 同步审计。
- `GET /api/site/occurrences/{occurrence_key}/quality`：
  提供当前最晚审核、完整历史、是否 stale，及身份/访问始终为 false 的声明。
- `POST /api/site/occurrences/{occurrence_key}/quality`：
  `verdict` 只能是 `valid_local_target`、`false_positive`、`needs_review`，
  `reason` 必须有实质说明；仅有权管理该场所的州/县级可写。
  不存在、已经移到范围外或不在有界投影窗口内的局部事件拒绝审核。
- `GET /api/site/quality/summary`：
  按当前可见、最多200条匿名 occurrence 汇总五种质检状态
  `not_reviewed, valid_local_target, false_positive, needs_review, stale_review`；
  明确不是独立人数、也不是到访次数。
- **证据签名**：
  - CAVIAR 使用来源视频哈希/场所/机位/局部轨迹号及相应的所有官方原始标注框位置和帧数；
  - Frigate 使用上游事件行的时间/置信度/快照存在与 `updated_at` 等字段。
  当帧位置、上游目标状态等发生变化，最新旧审核 `review_stale=true`、
  `quality_status=stale_review`，重新复核追加新行才能恢复有效状态。
- `/cctv` 原页面直接提供局部匿名目标选择、结论、理由、质量结果及历史；**不新建“第八类规则”或第二个人员底库**。
- 严格授权：州/县可审核辖区内发生的匿名局部事件；社区、街道可查看辖区内审核状态、不能提交。查询超出范围的对象返回404，避免跨县漏证。
- `tests/test_occurrence_reviews.py`：
  用真实 SQLite、FastAPI 鉴权、CAVIAR式原始 box 坐标修改和 Frigate 元数据更新
  测试审核追加、旧签名过期、历史保留、辖区和角色隔离、不可注入虚构人员底库。
- `scripts/browser_smoke.py`：
  实际浏览器先验证空状态不伪造监控事件；之后在 CI 控制的测试数据库显式插入
  **质量审核用 Frigate事件夹具**，浏览器通过有认证的真实 HTTP 接口提交质量审核，
  用数据库更新模拟上游证据变动，刷新后实际看到 `stale_review`。
  此夹具不是视频检测成绩；CAVIAR双机位真的视频解码证明仍引用独立 #38381692862。

## 还没有完成的“可信到访接口”

**本轮故意不让有效质检状态直接生成 Visit 或真实 SubjectLink。**
这不是遗漏，而是必须守住的人物身份边界：质量通过 ≠ 同一个自然人。

后续当有允许的、证据明确的主体关联/剧情事件真值时，
数据流建议在单独的、受控的模块实现：

`accepted_local_occurrence + trusted_subject_reference + event_clock + venue_binding
 → reviewed_subject_occurrence → deduplicated_visit → roster_candidate`

只有经专门验收的输入才能接到旧的 `base_registry` 与 `business`；
任何未通过质检/无人物真值的公开视频，不得被自动改成 P### 或真实风险事件。

## 已知边界

- 当前查询限定最多200条证据，超过需在下一阶段增加可分页索引/批处理。
- 质检是人工主观对局部检测质量的判断，不是自动模型的 precision/recall。
- 时间是原片帧号或UTC事件，无法自动将CAVIAR剪辑帧与当日监控时间合并。
- 人脸相似度、跨机位真实身份关联、个人关系推断不是本模块功能。
