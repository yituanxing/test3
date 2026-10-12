# ZD 场所视频分析 Demo — GitHub 实机集成验证

本仓库用于验证 **真实视频 → RTSP 流 → Frigate 人员目标检测 → FastAPI → SQLite → HTTP 查询**。

- 视频处理：复用开源 Frigate + MediaMTX + FFmpeg，不自行实现视频编解码或检测模型。
- 演示视频：从 Wikimedia Commons 下载由 Taurus Media Technik 发布、标记 **CC0** 的 *Video Codec Test pedestrian area 1080p25.y4m.webm*，仅做匿名目标检测，不做真人身份识别。
- 分析：计算 Frigate 真实产生的 `person` 事件。空事件不会被伪装成成功。
- 验证：GitHub Actions Linux runner 运行 Docker，测试实际 RTSP 解码、person 目标事件、API 同步、SQLite 落库以及重复同步幂等性。
- 安全：Frigate 未认证 5000 端口仅绑定本地回环；不上传个人身份库、人脸向量、原始场所敏感数据；视频测试内容不自动提交 Git 仓库。

**项目阶段：远程 CI 首轮集成。** 之前的本地 V0.8 业务项目有独立 ZIP，计划在视频链路验证稳定后整合；不要把本仓库现有测试脚本误认为已包含 Excel 的全部 23 项业务功能。

## 如何运行

在 GitHub 的 **Actions** 页面查看 `ZD Frigate Real Person CI`。配置支持 push 和手动触发。

本地（Docker 环境）：

```bash
python -m pip install fastapi uvicorn httpx
bash scripts/ci_verify_frigate.sh
```

## 测试素材和许可

- Wikimedia Commons：<https://commons.wikimedia.org/wiki/File:Video_Codec_Test_pedestrian_area_1080p25.y4m.webm>
- 原作者：Taurus Media Technik；页面标记 CC0 1.0。
- 视频约 15 秒，内容是人行区域走过镜头的人。CI 从 Commons 下载，不在 Git 中二次分发。
- CC0 解决作品版权授权问题，**不等于所有人物都同意生物识别或跨场所追踪**。测试仅验证匿名 person 目标检测；跨场所个人关联继续使用虚构测试身份。

有关运行状态，以 GitHub Actions 的实际日志和验证报告为准。
