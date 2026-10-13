# 多场所、多摄像机、人员底库统一模型审查 V1（2026-10-13）

依据：《ZD场所预警分析-原型设计-20261012-v1.0.xlsx》需求 1–23，重点 #1/#3/#4–12/#13–19/#20–23。用途：**冻结可复用的业务口径，先不新增模型/识别算法；日后更换视频素材、检测器和跟踪器不改业务规则**。

## 结论（只审计，不冒充已开发）

**流程、原型业务算法已设计并经过合成用例验证；统一的真实场景—人物—事件数据模型还没有全链落地。**

| 领域 | 当前已落地 | 实际缺口 / 必须保持的界线 |
|---|---|---|
| 辖区 / 业务场所库 | `places`、四级 `regions`、场所管理与权限。 | 同一场所内部的 **zone/scene** 暂无统一表；动漫使用独立 `anime_story_venues/groups`，不能当作正式 place 主键。 |
| 固定摄像机和媒体源 | `sources.place_id`、Frigate camera 绑定、GB/WVP session、真实 CAVIAR 两机位同场所。 | `sources` 是登记入口，不等于永久不变的物理设备；摄像机重绑、录制时间、media session 与历史场所绑定需统一审计/生效时间。 |
| 动漫移动镜头 | `anime_scene_segments`、`anime_story_venues`、`anime_story_scene_groups`、人工确认历史。 | 仅旧非主线；不将 shot = zone = venue，不把跨剪辑产生的视线变化当作人员移动。 |
| 真实观测/人物轨迹 | 视频样本、脸框、Frigate `(camera,event_id)`、CAVIAR `(source_id,camera_local_track_id)`。 | 上述是局部匿名跟踪、不是**跨摄像机真人身份**，不能直接供 #3 真人人员底库使用。 |
| Excel #3 人员归属底库 | `synthetic_registry_*`：P### 有效出现去重、时间/次数/人工、draft→published、重建/矫正、差异、周期维护、审计。 | 仅 synthetic_only；原始视频中真人尚无受控身份解析与一致的真值映射；不能把已有合成底库称作真实人员聚类。 |
| 业务层 | `business.analyze` 7 类合成规则，`rule_instances` 公共配置，`insights` 统计/模拟关系/检索。 | 查询和规则还依赖已知 P###，缺的是**可信主体 / 到访事件的输入适配层**；勿为了补模型重写规则、重新造第二套“人库”。 |
| 数据源/事实隔离 | `real_video`、`synthetic_only`、CAVIAR 官方标注各自有来源。 | 数据不得凭字段相似静默 JOIN；任何身份关联、置信度和人工复核结果须显式证据、版本及可撤销记录。 |

## 一、必须先对齐的 7 个概念

1. **Area / Region（辖区）**：州→县→街→社区；决定哪位账号能看、能管理。*不是*场所。
2. **Venue / Place（业务场所）**：学校 A、商店 B，Excel 所说“归属场所”的唯一主键。**场所 A 可有 N 路摄像机，N 个内部区域**；一个人从 A 教室到 A 校门**不属于跨所**。
3. **Scene / Zone（场景/区域）**：学校 A 校门、教学楼走廊、室内出入口等；多视角可指向同一 zone；一个场所可有多个 zone。特殊的动漫镜头可人工指定该镜头拍到的 zone。
4. **Camera / Source / Session（设备/媒体源/一次媒体会话）**：摄像机是物理观测通道，source 是系统逻辑登记，session 是实际连接/文件回放/GB 重连的具体版本；应保留 **effective_from/to 及源修订号**。一个 source 不应因为换绑定场所就改写历史。
5. **Detection / LocalTrack（检测框/摄像机内匿名轨迹）**：原始帧、人体框、局部跟踪段；只说明某相机画面中有匿名目标。键必须含 source/session，不能把两个机位相同的局部 track ID 合并。
6. **Occurrence / Visit（有效出现/到访）**：连续帧属于同一目标事件，仅算一次 occurrence；visit 还要求**有可信主体 + 明确业务场所 + 可信事件时间 + 会话时间规则**。没有身份则可统计轨迹/匿名目标事件，不能算独立真人到访。
7. **Subject / Roster（主体/底库快照）**：P### 只属于合成实验。后续在具有明确授权、证据、复核机制时才允许可信主体候选跨摄像机关联；Excel #3 的时间/频次/人工归属工作流应当可复用，而不是再造新的规则。

## 二、两种视频的差别：同一场所模型，两种来源定位

```text
固定摄像机：     Region → Venue A → Zone:校门 ← [Camera 1, Camera 2]
                                                    ↓
                        本机检测/匿名轨迹 → 有证据的出现事件

动漫历史素材：   Episode/MP4 → Shot 1 → 人工确认 Venue A / Zone:教室
                           └─── Shot 2 → 人工确认 Venue B / Zone:大门

这两者都不自动产生 global_subject_id。
```

- **固定监控**：摄像机位置固定，因此按有生效时间的绑定得到 venue/zone；历史数据写入当时绑定结果，重绑不改旧事件。
- **动漫**：片源/集数不属于唯一的业务场所；镜头的人工分类归入剧情场所与场景，**不**把媒体源的管理权限所属场所当剧情地点。
- **多场所**：A/B 分别有多台摄像机；仅在可信的相同主体分别有 A/B 事件时，才是跨所候选。
- **多摄像机同场所**：镜头 1 和镜头 2 同时看到一个人，只能先记录 **2 个相机内轨迹**；没有跨机位匹配证据，不得声称该场所“1 个去重真人”。
- **时间**：历史录像的录像时间、服务器入库时间、原片播放偏移、人工认定的剧情时间互相独立；模拟故事里的“24小时未出现”不能拿剪辑时长证明。

## 三、未来通用事件契约（建议新增 adapter，不是当前已存在的数据库表）

尽量复用当前 `places.id`、`sources.id`、`samples.id`、Frigate 事件和合成候选快照；**先定义契约，再考虑数据库迁移**。

```text
Place             id, region_id, active
Zone              id, place_id, name, type, status
SourceBinding     source_id, camera_id?, zone_id?, place_id,
                  valid_from, valid_to, revision
MediaSession      source_id, session_id, content_digest?,
                  media_time_base, started_at, ended_at
Observation       id, source_type, source_id, session_id,
                  frame/offset, observed_at?, ingested_at,
                  place_id?, zone_id?, evidence_ref
LocalTrack        source_id, session_id, local_track_key,
                  first_seen, last_seen, detection_ids
Occurrence        occurrence_id, local_track_refs, place_id,
                  time_basis, evidence_refs, quality
SubjectLink       subject_namespace, subject_key, occurrence_ids,
                  linkage_basis, reviewer?, evidence_revision,
                  state=[unknown,candidate,confirmed,rejected]
Visit             subject_ref, place_id, valid_from/to, occurrence_refs,
                  session_rule_revision, evidence_status
RosterSnapshot    run_id, period, strategy, correction_mode, state,
                  candidates, decisions, diff, audit
Alert             rule_instance_id, visit/occurrence_refs,
                  evidence_signature, review_state, stale
```

关键：`subject_namespace` 要将 `synthetic:P001`、人工标注的 research-local person 和未来有相应授权的真人身份分别隔离；**没有经过确认的关联，就保持 unknown**。人脸特征、肖像识别的存储/比对不在本期预置能力内。

### 复用原则

- `SourceBinding/MediaSession` 只负责“画面来自哪里、它当时在哪个区域”；
- `Detector/Tracker` 只负责“画面里连续出现了一个匿名目标”；
- `SubjectLink` 才有权表述“这些出现属于同一主体”，须有可核验的证据；
- `VisitBuilder` 才有权合成“该主体访问某业务场所”，而且保留 occurrence IDs；
- `RosterSnapshot` 完全复用已有的“生成候选→裁决→发布→矫正”设计；
- `insights/rule_instances` 尽量输入规范化 `Visit` 与已发布 `RosterSnapshot`，而不是知道视频来自 Haar、Frigate、GB 还是 CAVIAR。

## 四、Excel #3 核心验收逻辑是否可复用？

**是。** 当前针对已知 P### 真值：
- 指定天数、首次出现、按 `occurrence_key` 去重的有效出现次数；
- 时间优先、次数优先、人工指定；
- 重新生成 / 保留前版人工裁决的 reconcile；
- 未再出现的人从新生成的有效底库退出；
- 草稿→发布→归档，旧快照与差异保留；
- 一旦来源观测改判，草稿/发布版本 stale；不同版本不能暗改。
  
但对**真人**依然缺：身份来源、跨摄像机匹配的可靠证据及审核、同人重复观察去重的可接受真值、异常/拒绝的处理。当前 CAVIAR 同一走廊两机位有人工**局部轨迹**，不能直接作为跨摄像机身份对应真值。

## 五、仅冻结设计，后续实现按验收门槛推进

**工程上必须先补齐的四处公共底座（不是七个页面）：**

1. **Place–Zone–SourceBinding 的统一归属与历史版本**：可配置同场所多摄像机、多个区域，重绑不会污染已有观察证据；动漫的 `story_venue` 与真实 place 不应在未明确迁移方案前强制混表。
2. **统一 Observation/LocalTrack/Occurrence 的只读投影**：来自相机和文件的视频事件有可追溯的 frame/事件键，原始事实不可覆盖，帧与目标段分层计数。
3. **身份链接与 Visit 的独立接口 + 明确 unknown**：没有身份真值先保持匿名；未来有人为确认的研究数据适配时才允许进入人次聚合。不能将 local_track_id 拼接后冒充跨机唯一 subject。
4. **将现在 synthetic-only 的 registry/statistics/rules 改为接收经验证的上游 visit 契约**：保留现有的演示代码/回归用例；不要在新真人路线复制一套“底库”和规则。

**门禁真值用例（低成本但能揭示根本错误）：**
- A 场所 2 个摄像头同一时间分别 track#1，不自动成为同一人。
- A 场所不同 zone 人物移动，不触发串所；A→B 只有在受信身份确认后才产生跨所候选。
- 摄像机先绑定 A 再绑定 B，旧事件 venue 永久仍为 A。
- 100 帧同一 occurrence 计1次出现；不同 occurrence 不因为同人而抹掉。
- 人工修订一条链接后所有依赖的 visit/底库/告警版本应可见 stale；源视频/原始观测不动。
- CAVIAR corridor/front 同一走廊，属于同一 place、2 local camera tracks，不提供跨机同人断言。
- 合成 P### 数据无法通过插入真实 frame/box 直接创建一个“真实人”。

**结论：现在应当暂停扩大检测器/跟踪器、地图图层或新业务页面的范围；这几类是可单独补充的插件，不需要重写 Excel #3 主归属逻辑。先守住上面四处共有数据契约，再逐步加入真实检测/跟踪。**

## 当前实现与证据

- `app/base_registry.py` / `app/roster_maintenance.py` / `CORE_VIDEO_ROSTER_SPEC_V1.md`
- `app/business.py` / `app/rule_instances.py` / `app/insights.py`
- `app/anime_ontology.py` / `app/anime_scenes.py`
- `app/cctv_dataset.py` / `app/main.py`
- 真实 CAVIAR 双机位媒体 CI：[#38381692862](https://github.com/yituanxing/test3/actions/runs/38381692862)；实际 383帧 ×2 与官方标注已验证。但该 CI **不是**跨摄像机真实身份库验收。

本 ADR 仅复核和冻结语义，**不修改业务表或启用真人身份匹配**。
