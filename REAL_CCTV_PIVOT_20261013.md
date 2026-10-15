# 真实固定监控演示优先：从动漫转为真人研究视频（2026-10-13）

## 用户新决策

**P0 演示使用获得明确公开许可的真人固定监控研究视频，不再把动漫场景归并作为主入口**。保留已有的 `/anime-scenes` 作为非主线的剧情场景实验，避免删掉已验证成果，但门户和业务页面默认引导 `/cctv`。

原 Excel 设定（固定监控）比动漫简单：**一处场所可有多台摄像机、每台摄像机位置固定、人物经过画面。** CAVIAR Lisbon 数据集有同一条商场走廊两个同步机位：corridor 和 front。它们都绑定到**同一个**管理场所，不能人为拆成“场所A/B”来凑跨场所预警。

## 这轮真正落实的能力

1. `app/cctv_dataset.py`：CAVIAR 官方的**HTTPS 白名单**双机位/固定场所数据集注册，只允许 `enter_exit_crossing_paths_1` 两个视角。服务器真正下载官方 MPEG2 和人工 XML（限制视频30MiB、标注5MiB），OpenCV 实际解码、计算 SHA-256，下载失败返回错误而不是伪造成功。
2. 复用现有 `sources.place_id` 绑定同一场所；两个不同 `source_id` 分别代表机位。下载文件只存在 `ZD_DATA_DIR/public_cctv`，**不进入公开源码 ZIP 或 Git 仓库**。可在相同场所重复导入时幂等复用。
3. `public_cctv_track_boxes`：导入原始 CAVIAR CVML XML，保留“某摄像机第N帧”的人工人体框、来源和**该摄像机内局部 track_id**。这些不是我们模型的预测，不是自然人身份，不能把 corridor ID 0 与 front ID 0 认作同一个人。
4. `/api/cctv/sources/{source_id}/frame?frame=N`：实际 OpenCV 解码此帧，将 CAVIAR **人工框**用绿色覆盖并显示编号及明显来源水印。无数据不能产生伪造的图像。
5. `/cctv` 页面：由真实场所入口导入双机位、查看已导入源、原始视频帧、人工框/机位局部轨迹，可单独调用现有匿名人脸候选检测（它不是人体检测器，低分辨率可能为零，不应误判无人）。
6. 现有多级权限校验：导入为州级管理员操作；各级仅查询授权辖区内的视频、活动和帧。仓库原有认证、监控、GB28181/RTSP、底库、规则配置保留；不新增重复工作流。

## 公开真实数据依据及版权

- CAVIAR 官方目录：https://groups.inf.ed.ac.uk/vision/DATASETS/CAVIAR/CAVIARDATA1/
- 下载均限定：https://groups.inf.ed.ac.uk/vision/DATASETS/CAVIAR/CAVIARDATA2/
- EC Funded CAVIAR Project / IST 2001 37540，Creative Commons **BY-SA**。如公开展示截图或加工视频请保留来源和适用的相同方式共享要求。
- `EnterExitCrossingPaths1cor.mpg` / `EnterExitCrossingPaths1front.mpg`，官方列出各约 2MB；分别有 `ceecp1gt.xml`、`feecp1gt.xml` 人工标注。
- 更大型多场所数据未来可使用 MEVA（官方 CC BY 4.0，https://mevadata.org/），但这次不下载几百GB；先把双机位固定场所链路验真。

## Excel 23 项如何分阶段验证

- **真实媒体验收可先验证**：#1 两路真实视频文件与处理、#4 原始真实帧/机位计数、#13/16 画面证据与时间、#20 管理场所与视频源。
- **视频层当前不宣称已解决**：动画角色→真人的自动身份识别、多人跨摄像机唯一身份归并、真实跨场所 #6–9、真实缺席 #10、人数偏差 #12。CAVIAR *同一* 走廊的两机位不能证明跨**不同场所**。公开人工框只能验标注/检测结果，不能把视频直接送入合成 P### 人员底库。
- **仍可保留既有可操作的合成业务**：#3 和 #5–19 的 P### 模拟规则及审核；前端和证据字段必须继续标注 `synthetic_only`，不得因为替换素材就偷换成真实身份分析。
- **下一关键关口**：在真实 CAVIAR 双机位验证人员**检测与单机位匿名跟踪**性能（以人工 XML 为 GT），再研究经人工/基准数据确认的跨机位去重。只有有明确跨不同场所身份 GT 的 MEVA/MEVID 类型场景才可以验证 Excel 的真实跨场所身份链路。

## 验收

- `tests/test_cctv_dataset.py`：使用仓库本地实际可解码测试视频（不冒称 CAVIAR 真片）覆盖授权、官方来源白名单、CVML 解析、帧 JPEG 解码、两个机位属于同一场所、不得跨摄像机直接使用局部 ID。
- `scripts/browser_smoke.py`：Chromium 验证 `/cctv` 新首选入口和“尚未导入真实研究视频”时诚实空状态。
- `scripts/verify_caviar_public_footage.py`：由 PR 的 **`cctv-real` 标签显式触发**现有 backend job 的官方两段真实 MPEG+XML 下载、两机位实际解码及 JPEG 人工框证据。没有增加新的 workflow/job；由于官方站点网络可用性不保证，必须看本轮真实结果后才说视频已验收通过。CI artifact `public-caviar-actual-video-proof` 只保存授权来源信息、SHA256、帧数和小截图，不分发原始 MPEG。
