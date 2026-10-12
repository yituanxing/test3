# 业务 Demo 与真实视频采集链的关系

业务服务：`python -m uvicorn app.main:app --host 127.0.0.1 --port 8001`。

前端页面：`/`, `/monitor`, `/admin`, `/business`, `/base`；接口：`/docs`。使用 `requirements-business.txt`。

真实视频事件的接入（须在 Frigate 运行中）：

1. 设置 `ZD_FRIGATE_API=http://127.0.0.1:5000`（Frigate 的 **未认证** API 端口仅回环绑定）。
2. 业务服务调用 `/api/places` 获取场所 ID。
3. POST `/api/frigate/cameras` with `{"camera":"demo","place_id":1}`。
4. POST `/api/frigate/sync` 从运行中的 Frigate 拉取 `person` 目标事件，并按事件 ID 幂等入库。
5. GET `/api/frigate/events`, `/api/frigate/summary`, `/api/frigate/quality` 检查结果。
6. 人工修改模拟规则仅影响合成身份场景，不应改变真实事件的身份归属。

严格分界：目标检测 event != 身份识别，也不是独立人数。`/api/frigate/quality` 对查准率、召回率返回 null，直至拥有独立标注的真值数据。模型 score 也不是准确率。切勿在公开仓库上传私人录像、人脸照片、真实身份数据。

CI: 本仓库同一 workflow 下分别执行业务30+测试和 Frigate 真推理/RTSP 验收。成功的媒体验收要求真实 person 事件来自上游实际 API，不能模拟出结果。


底库归并演示：见 `BASE_REGISTRY_V0.9.md`；/base 为模拟人物底库的版本化生成、人工归属、增删矫正与发布，和匿名实际 Frigate person 事件隔离。


## V1.0 态势与检索

打开 `/insights` 进入独立合成身份态势分析，支持图表、场所筛选、按 P### 编码检索、人员时间线、基于模拟同地同时段的共同出现关系，以及分开的真实视频匿名检测计数。详见 `INSIGHTS_V1.0.md`。
