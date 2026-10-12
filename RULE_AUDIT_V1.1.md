# V1.1 - Excel 规则口径验收（合成演示）
对应源文件 ZD场所预警分析-原型设计-20261012-v1.0.xlsx 的第3、6-12、22项。
**真实 Frigate person 仅是匿名目标检测；以下 P001 等虚构身份规则与真人数据严格隔离。**

## 本轮完成
- 第3项频率归并：按原始有效观测次数，不再用去重到访次数替代。
- 第8项互动：增加 interaction_min_rounds；双向出现次数取 min(A主场,B主场) 才算完整往返轮次。
- 第10项久未出现：absence_mode=days 或 missed_checks；可设定检查间隔与连续缺席周期门槛。无观测绝不等于真实人员缺席。
- 第12项人数偏差：配置 activity_start_hour、activity_end_hour、activity_timezone_offset_hours；只有活动结束后才评估指定时间窗内去重合成身份与底库规模差异。
- 第22项预警管理：enabled_rules 控制七类规则开启/暂停，修改保留原始事件、保留审核证据。
- 配置 API 严格拒绝未知/重复规则、非法时段和越界参数。
- `/business` 页面全部可配置并展示暂停复选框。

## 未完成
GB28181、人脸识别身份关联、真实访问鉴权、预警规则多实例创建/删除、真实人数精准计数；不得宣称这些能力已经实现。

## 运行
`pip install -r requirements-business.txt && python -m pytest -q`
`uvicorn app.main:app --host 127.0.0.1 --port 8001`
浏览器访问 `http://127.0.0.1:8001/business`。

最新 GitHub Actions 结果以对应提交的实际工作流为准。
