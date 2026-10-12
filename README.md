# ZD 场所视频分析 Demo — GitHub 实机集成验证

本仓库用于验证 **真实视频 → RTSP 流 → Frigate 人员目标检测 → FastAPI → SQLite → HTTP 查询**。

- 视频处理：复用开源 Frigate + MediaMTX + FFmpeg，不自行实现视频编解码或检测模型。
- 演示视频：从 Intel sample-videos 仓库下载采用 **CC-BY-4.0** 许可的 *one-by-one-person-detection.mp4*（真实行人、用于人员检测回归），仅做匿名目标检测，不做真人身份识别。
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

- Intel sample-videos：<https://github.com/intel-iot-devkit/sample-videos/blob/master/one-by-one-person-detection.mp4>
- 项目及原始素材许可证：<https://github.com/intel-iot-devkit/sample-videos/blob/master/LICENSE>（CC-BY-4.0，保留出处署名）；仅在 CI 中下载，不将视频提交进本仓库。
- 先前 Wikimedia Commons 的 CC0 行人素材仍可作第二视频源测试；该素材虽有大量人物，但 Frigate 测试尚未产生事件。换视频是为了隔离画面/模型因素，不等于诊断完成。
- 视频素材的版权许可不等于所有人同意人脸识别或跨场所身份追踪。本回归只验证匿名 person 目标检测，身份关联业务继续使用合成数据。

有关运行状态，以 GitHub Actions 的实际日志和验证报告为准。
