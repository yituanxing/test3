# ZD 场所分析演示系统

本仓库提供两套语义隔离的可运行服务：

1. **业务Web平台**：`python -m uvicorn app.main:app --port 8001`。页面：`/admin`、`/business`、`/monitor`；行政区域、场所与设备管理、视频观测、合成规则、审核和纠错。
2. **Frigate技术验收桥**：`python -m uvicorn app.bridge:app --port 8000`，供 Docker/RTSP/OpenVINO 实机验收使用。

## 最终业务演示入口（V1.3）

- `/login`：登录后进入 `/portal` 综合态势门户；包含授权辖区场所列表、场所真实匿名检测统计、合成预警中心、规则证据详情和州级复核。
- `/portal?tab=places`：场所列表；`/portal?tab=alerts`：模拟规则候选；与原有管理工作台互相跳转。
- 首次启动需在本机通过 `python -m scripts.bootstrap_roles --username demo_state --region-id 1` 创建州级演示用户，并输入至少12位密码。区域 ID 以 SQLite 实际数据为准。
- 视频生产链路仍需 Frigate/MediaMTX。主 Web 真实事件闭环由最新 `scripts/verify_portal_live.py` + GitHub Actions 验证。
- 真实匿名人形目标事件与合成 P### 身份、到访、预警严格分离；不得宣称已经实现真实人员身份识别。

安装及运行：

```bash
python -m pip install -r requirements.txt -r requirements-business.txt
pytest -q
python -m uvicorn app.main:app --port 8001
```

完整业务操作和限制见 [DEMO_INTEGRATION.md](DEMO_INTEGRATION.md)。测试素材授权与上游依赖见 [THIRD_PARTY.md](THIRD_PARTY.md)。

**检测质量说明：** Frigate 在 [CI #38213089455](https://github.com/yituanxing/test3/actions/runs/38213089455) 上已通过 Intel 公共样例视频→FFmpeg→MediaMTX→RTSP→Frigate/OpenVINO→FastAPI→SQLite 的完整链路，产生2条真实 `person` 目标事件并幂等入库。**2条检测事件不是2个不同的人，不能据此估算查准率、召回率或准确识别人员身份。** 真实人物身份匹配未验证；涉及跨场所身份的业务规则只使用虚构人员。

**数据合规：** 公开仓库只包含许可证允许的演示素材，不存储生产场所录像、真实人员身份、人脸向量、秘密凭证。
