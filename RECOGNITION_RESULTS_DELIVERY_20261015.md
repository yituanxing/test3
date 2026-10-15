# 视觉识别研发成果清单与交付演示（2026-10-15）

> 目的：把此前已经完成的真实图像处理、模型推理、跨镜头研究、模型验证、视频接入成果，明确地放进 **网站 + 可讲解的成果材料**。不是把真实视频仅当首页背景，也不允许把 P### 模拟人员冒充真人识别。网站入口：`/recognition-results`。

## 一、识别成果与证据归属（不是一套假数据）

| 成果 | 输入 | 实现与产出 | 现在能展示 | 尚不能声称 | 可回查 |
| --- | --- | --- | --- | --- | --- |
| 真实 CAVIAR 画面解码与官方人体标注核对 | 爱丁堡 CAVIAR 公开双机位 MPEG/XML | 原片逐帧解码、官方CVML人体框、**YOLO11n独立模型预测框**、Roboflow ByteTrack/OC-SORT 单机位追踪、人工质量复核 | `/cctv#caviarImport`，原片帧与模型框（需联网导入授权媒体/模型） | 官方GT框不是模型预测；单机位track_id不是跨机真人身份。384×288低分辨率场景中部分镜头检出率明显不足 | `scripts/verify_caviar_public_footage.py`、`app/tracker_adapters.py`、原有 Owner CI |
| EPFL 双摄像机同一实验室同步人体配对 | 官方 `6p-c0.avi`、`6p-c1.avi`、标定文件及官方稀疏评测真值 | YOLO11n检测→地面标定→Hungarian一对一候选匹配→SQLite候选与人工审核 | `/real`、`/cctv#epflResearchPanel`、`/recognition-results` | 不是脸识别/任意非重叠场所ReID/独立去重人数 | `EPFL_6P_CROSS_CAMERA_ACCEPTANCE_20261014.md`、Owner CI [#38503988215](https://github.com/yituanxing/test3/actions/runs/38503988215) |
| 真视频匿名活动+演示触发 | 上述 EPFL 同步模型候选 | 119条同步配对观测；基于保守时间条件的匿名短片段；空间网格及录像秒定位；演示规则有真实来源候选键 | `/insights` 默认真实活动、`/real` 视频、空间和演示提醒 | 119≠119个人；短片段≠可持续确认的自然人；没有真实跨场所到访 | `app/real_activity.py`、`app/real_scene_aggregate.py`、浏览器 CI [#38659950549](https://github.com/yituanxing/test3/actions/runs/38659950549) |
| 实时/国标媒体处理 | RTSP、Frigate、WVP/ZLMediaKit 国标适配 | 视频源绑定、持续采集、真实匿名事件持久化；媒体转发链路和适配器测试 | `/monitor` 实际状态与设备列表 | HTTP适配通过≠现场IPC/SIP REGISTER/INVITE全流程验收 | `deploy/GB28181_WVP_GUIDE.md` |
| 可选授权照片人脸相似度 | 由操作者明确授权提供的单张真人照片 | OpenCV Zoo YuNet 单脸检测 + SFace短时1:N相似排序；会话内临时缓存，限制样本数和保留时间 | `/face-lab`（需管理员显式启用且自行安装经授权权重） | 当前不具备监控真人脸库与跨所真实人脸检索；模型许可待核定 | `FACE_PHOTO_LAB_V0_20261014.md` |
| 动画镜头和虚构角色场景 | 授权动画文件（可上传） | OpenCV视频剪辑点候选、人工命名场景、场景关系图、证据约束的人工角色标记 | `/anime-scenes` | 不是已经训练成功的自动动画角色ReID或实际地理轨迹 | `ANIME_SCENE_MAP_V0.1.md` |

## 二、真实研究指标（必须给出限定口径）

- EPFL 在 **41个**被选取同步时刻（基线21个+扩展20个）得到 **119条**有官方真值可评分的同帧配对；这119条与官方同人标签一致、在当次可评分配对里未发现错配。
- 双侧均检出的123个可评估对应里，自动匹配覆盖 **119/123**。该比值**不能**当作全视频人体检测召回率、人脸识别率、跨场所身份证明或生产准确率。
- 活动层默认显示真实匿名片段；跨时关联仅限通过独立真值门禁的短跨度。任何候选审核删除会使旧关联证明失效、相关演示提醒关闭；不会把V编号直接加入P###人员底库。
- 真视频上的“空间网格、区域提醒”是匿名检测坐标和人工演示阈值，不是 GPS 或真实两个物理场所之间的人员移动。
- 研究页面可能出现 **105个匿名候选片段，其中14段包含经核验的相邻时间关联**；这些是时间分段不是去重人数。以当前数据库 `/api/real/activity` 输出为准。

## 三、为什么发布包没有把所有模型权重都塞进来

1. **用户现场演示**：完整 Windows 源码演示包可以携带经过 Owner CI 真正模型推理生成的匿名候选JSON、授权范围内的短视频摘录，供网页直接加载。**无需现场重新运行YOLO**，是最大化复用实验成果、避免临场网络下载失败的正确做法。
2. **研发复现实验**：如果要从两台完整原始AVI重新跑YOLO/ByteTrack/几何标定，应在有网络、授权和可复现依赖环境中运行现有 `scripts/verify_epfl_six_person_ground_truth.py` / `scripts/verify_caviar_public_footage.py`，使用 `requirements-person-detection.txt` 依赖。原片、权重的获取遵循各自来源许可，不能声称打包的是完整原始实验媒体。
3. **人脸可选实验**：`python -m scripts.install_yunet`、`python -m scripts.install_sface` 下载OpenCV Zoo模型；显式设置 `ZD_FACE_LAB_ENABLE=1` 后，再运行 `python -m scripts.verify_face_photo_lab_local` 进行真实模型自检。没有下载到权重时UI必须显示缺失，而不是示假数据或给出虚假的通过标志。**授权和第三方权利审查未完成，禁止把权重默认为可商用重分发。**
4. **国标现场部署**：WVP和ZLMediaKit是外部组件，需要部署、真实或合法模拟GB设备及合法秘钥；打包网站不会自动拥有生产摄像机。见 `deploy/GB28181_WVP_GUIDE.md`。

## 四、给展示者的最短讲解路线（5分钟左右，按页面顺序操作）

1. **成果总览** `/recognition-results`：展示“真实研究项目而不是HTML造图”，讲清119个配对观测、数据来源及模型。
2. **真视频** `/real`：播放38秒双摄像机摘录，选中录像的真实检测时刻，看模型/匿名活动及空间分布。可以解释原始检测事实与演示阈值的区别。
3. **匿名活动记录** `/insights`：默认展示V###真实视频匿名片段，点源证据回`/cctv#epflResearchPanel`；强调不等于确认自然人。
4. **算法与人工质量核查** `/cctv`：展示同帧配对源frame/box、人工审核；可选 CAVIAR原片与YOLO/ByteTrack，需额外媒体安装。
5. **业务产品闭环** `/portal` → `/base` / `/business`：说明这是 P### 合成业务场景，用来验证七类规则和复核，**不是上面V###真实检测自动生成的真人告警**。
6. **补充技术** `/monitor`、`/face-lab`、`/anime-scenes`：展示已经投入的媒体、可选模型和动画研究，不夸大现状。

## 五、Excel原需求的诚实验收边界

原23项中：管理类已验3项、合成场景10项、部分实现8项、原范围缺口2项（人脸检索及真人地理轨迹）。其中“合成10项”是真实可运行规则引擎+虚构人员输入，绝不冒充识别出的真实个人。实时现场摄像机未接入时 Frigate person 计数应为0。

本成果文档和网站新页面只**汇总已经做过的识别与工程价值**，不试图借宣传把尚未通过的能力改成已验收。正式商用部署前还需确认EPFL/CAVIAR等原始研究素材许可、Ultralytics模型依赖许可及个人信息合法处理条件。

## 验收证据入口

- 模型与双机Owner: https://github.com/yituanxing/test3/actions/runs/38503988215
- 实际产品Chromium和视频: https://github.com/yituanxing/test3/actions/runs/38659950549
- Excel状态逐项依据: `config/excel_acceptance_v1.json`
- 有效图像处理入口: `/real`, `/cctv`, `/insights`; 统一成果：`/recognition-results`
