# V1.2 · 四级辖区认证与权限控制（本地演示候选）

## 方案选型

没有引入第二套用户中心或大型 RBAC 框架。复用 FastAPI / Starlette 的请求与路由体系；密码哈希使用维护成熟的 `argon2-cffi`，随机 Bearer Token 使用 Python `secrets`，会话仅以 SHA-256 摘要存入 SQLite，并支持退出及 8 小时到期。辖区树沿用已有 SQLite `regions` 四级结构。

此设计是**最小可验收权限底座**，而非已通过渗透测试的互联网级身份管理系统。`FastAPI Users` 处于维护模式且引入额外用户持久化/异步依赖，对本项目单机 SQLite 演示不合算；其现成功能没有替代我们的**辖区对象授权**。

## 启动与初次配置

从项目目录运行：

```bash
pip install -r requirements-business.txt
python -m scripts.bootstrap_roles --username demo_state --region-id 1
python -m scripts.bootstrap_roles --username demo_county --region-id 2
python -m scripts.bootstrap_roles --username demo_street --region-id 3
python -m scripts.bootstrap_roles --username demo_community --region-id 4
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

实际区域 ID 请通过本机 SQLite 的 `SELECT id,name,level FROM regions` 查询确认；上面 1/2/3/4 只适用于默认空数据库初始化。账号的角色是绑定区域的 level，密码在交互终端输入，不写入仓库。通过 `http://127.0.0.1:8001/login` 登录。已有六个前端页面自动附带 `Authorization: Bearer <token>`，界面上展示当前用户名、辖区和退出按钮。

## 实现与验证边界

| 角色 | 范围查询 | 业务操作 | 权限策略 |
|---|---|---|---|
| 州级 Level 1 | 所辖县市及下级 | 管理角色：保留原有业务功能 | 尚未覆盖跨州多租户隔离，限制在单州演示数据 |
| 县市级 Level 2 | 所辖街道、社区及场所 | 管理辖区内场所和源配置 | 跨辖区新增、移动和修改会被后端拒绝 |
| 街道级 Level 3 | 所辖社区及场所 | 只读态势、区域、场所和允许检索接口 | 未逐项审核的高级业务接口拒绝 |
| 社区级 Level 4 | 本社区场所 | 与街道相同的辖区只读功能 | 未逐项审核的高级业务接口拒绝 |

- 以 **服务端数据库中用户绑定的 region_id** 计算递归子树；**不信任客户端传入的 region_id** 充当权限。
- `/api/regions`、`/api/places`、`/api/sources`、`/api/monitoring/overview`、`/api/frigate/events`、`/api/frigate/summary`、`/api/frigate/cameras`、`/api/insights/*` 的允许读接口经服务端辖区过滤。
- 允许的县市级写操作在源/目标场所两侧分别检查归属；使用 403 防止跨辖区访问。
- 下级用户不可使用尚未逐项按区域重构的 `/api/base/*`、`/api/business/*` 和全局管理员审计等高敏感接口，属于**安全优先的暂不开放**，而不是声称已完整实现。
- 源地址里的 URI 可能包含凭证：对县市及下级账号从列表中遮蔽。
- 默认所有 `/api/*`（除 `/api/health`、`/api/auth/login`）均需要认证；拒绝未知作用域的请求。
- 旧版自动化测试通过 `tests/conftest.py` 中的 **仅在真实 pytest 测试过程才有效** 的迁移兼容标志运行，新的 `tests/test_access_v12.py` 每项都会关掉该标志并实测真实认证路径。
- 未实现：站点部署 TLS、密码重置、锁定/限速、双因素认证、完整多州权限、多组织用户管理 UI、管理员审计按辖区分表、跨页菜单按角色隐藏。无法作为生产级权限控制认证。

## 测试

```bash
python -m pytest -q
```

本地全部 64 项测试通过，包括 9 项 V1.2 新增授权测试（未登录 401、越区 403、写端权限检查、密码错误、退出撤销、URI 密码遮蔽等）。

## 技术边界

本项目以合成身份分析为演示，不实现跨视频/跨摄像机的真实身份识别。`person` 检测结果不是去重人数。原始 23 项功能要求中的真实 GB28181 对接、身份聚类、真实人员底库均未完成，不可对外宣称已交付。
