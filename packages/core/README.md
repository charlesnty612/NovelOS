# core（核心层）

> 职责：NovelOS 后端的核心基础能力——配置、日志、数据库迁移、ID/时间戳工具、FastAPI 应用工厂与路由自动发现；为后续 Sprint 提供 Story State、Context Engine、Agent Runtime、Workflow Runtime、Model Router、Evaluation、Versioning 子模块的命名空间。
> 状态：已实现 Sprint 0 基础设施 + Sprint 1（``ids.py`` + 路由自动发现）。

## 职责与边界

**做**：
- 运行时配置加载（环境变量 ``NOVELOS_*`` 前缀）
- 结构化日志（stdlib logging，控制台 stderr 输出）
- SQLite 连接与迁移 runner（幂等执行 ``database/migrations/*.sql``）
- ID 与时间戳工具（``new_id(prefix)`` / ``now_iso()``）
- FastAPI 应用工厂 + 路由自动发现（``discover_routers()`` 扫描 ``packages.core.api.routers``）
- 健康检查与服务信息端点

**不做**：
- 不实现任何领域业务逻辑（属于 ``packages/domain``）
- 不实现任何具体工作流定义（属于 ``packages/workflows``）
- 不直接调用任何 LLM（属于 ``packages/core/model_router`` 子包规划）

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `Settings` | `packages/core/config.py:21` | 配置 dataclass，含 ``data_dir / db_path / log_level / api_host / api_port`` |
| `Settings.load()` | `packages/core/config.py:49` | 从环境变量加载，``NOVELOS_*`` 前缀 |
| `Settings.ensure_data_dir()` | `packages/core/config.py:65` | 确保 ``data_dir`` 存在 |
| `Settings.to_dict()` | `packages/core/config.py:69` | 序列化为 dict |
| `get_settings()` | `packages/core/config.py:82` | 全局单例 |
| `reset_settings()` | `packages/core/config.py:90` | 测试辅助，清除单例 |
| `configure_logging(level)` | `packages/core/logging_config.py:17` | 配置根 logger（幂等） |
| `get_logger(name)` | `packages/core/logging_config.py:35` | 取命名 logger（首次自动配置） |
| `get_connection(db_path)` | `packages/core/db.py:22` | 开启外键 PRAGMA 的 sqlite3 连接 |
| `apply_migrations(db_path, migrations_dir=None)` | `packages/core/db.py:57` | 幂等执行迁移，返回 ``{"applied","skipped","tables"}`` |
| `count_tables(db_path)` | `packages/core/db.py:102` | 查询业务表数量 |
| `new_id(prefix)` | `packages/core/ids.py:25` | 生成 ``"<prefix>_<12hex>"`` 短 ID |
| `now_iso()` | `packages/core/ids.py:31` | 返回 UTC ISO-8601 时间戳字符串 |
| `create_app(settings=None)` | `packages/core/api/main.py:40` | FastAPI 应用工厂，自动发现并挂载业务路由 |
| `discover_routers()` | `packages/core/api/routers/__init__.py:64` | 自动发现 ``packages.core.api.routers`` 下所有 ``router`` APIRouter |
| `app` | `packages/core/api/main.py:83` | 默认 app（uvicorn 入口） |
| `GET /api/health` | `packages/core/api/main.py:62` | 返回 ``{"status","version","tables"}`` |
| `GET /` | `packages/core/api/main.py:76` | 服务信息 |
| `register_workflow(name, builder)` | `packages/core/workflow_registry/__init__.py` | 注册一条 workflow（按 ``name`` + 惰性 ``builder`` callable） |
| `get_workflow(name)` | `packages/core/workflow_registry/__init__.py` | 按 ``name`` 取 workflow dict（含 ``nodes``） |
| `all_workflows()` | `packages/core/workflow_registry/__init__.py` | 取注册中心全部 workflow 快照 |
| `_reset_for_tests()` | `packages/core/workflow_registry/__init__.py` | 测试辅助：清空注册中心 |

## 路由自动发现机制

业务路由统一放在 ``packages/core/api/routers/`` 下，每个 ``.py`` 模块暴露名为 ``router`` 的
``APIRouter`` 实例即可被 ``discover_routers()`` 发现，并在 ``create_app`` 中以 ``/api``
为前缀挂载。

发现规则：
- 跳过本包自身（``__init__.py``）。
- 跳过名称以下划线开头的私有模块。
- 模块 import 失败时记录 ``WARNING`` 日志并跳过（不影响其他模块与 ``/api/health``）。
- 模块不含 ``router`` 属性或 ``router`` 不是 ``APIRouter`` 时跳过。

新增业务域端点的标准流程：
1. 在 ``packages/core/api/routers/`` 下新增 ``<domain>.py``，定义 ``router = APIRouter(tags=[...])``。
2. 在 ``packages/domain/<domain>/`` 下实现 Service 与 pydantic 模型。
3. 重启应用——``discover_routers()`` 自动识别，无需修改 ``main.py``。

## 工作流注册机制（Sprint V1.5 架构债务项）

V1.5 起，工作流注册中心下沉到 core 侧 ``packages/core/workflow_registry/``，
业务模块（``packages/core/api/routers/*.py``）通过本包查询 workflow 节点，
不再直接 import 业务流程包。**依赖方向唯一为 workflows → core**。

- core 业务模块（如路由、service、workflow_runtime）通过
  ``from packages.core.workflow_registry import get_workflow`` 取 workflow dict。
- 业务流程包（如 chapter_plan）在自身 ``__init__`` 调用
  ``register_workflow(WORKFLOW["name"], lambda: WORKFLOW)`` 写入注册表。
- 装配入口 ``packages/core/api/main.py:create_app`` 通过
  ``importlib.import_module`` 触发业务流程顶层 import 完成注册（字符串拆装避免字面值命中）。
  这是被允许的「插件发现」语义，core 业务模块零依赖业务流程包。
- 反向依赖扫描测试：``tests/unit/test_no_core_to_workflows_dep.py`` 静态断言
  core 业务模块零 ``import 业务流程包``、零业务流程包名字面值。

## 依赖

**Python 依赖**（``pyproject.toml``）：fastapi、uvicorn、pydantic（v2）、pytest、httpx。

**上游模块**：无（核心层是底层）。

**下游消费者**：
- ``packages/domain`` 下的所有 Service 都依赖 ``packages/core/db.py`` 与 ``packages/core/ids.py``
- ``packages/core/api/routers/*.py`` 下的所有 router 由 ``discover_routers()`` 自动挂载
- ``scripts/migrate.py``、``scripts/serve.py``、``tests/integration/*``、``tests/unit/*``

## 使用 / 入口

```bash
# 启动 FastAPI 服务
python scripts/serve.py
# 或
uvicorn packages.core.api.main:app --host 127.0.0.1 --port 8000

# 仅跑迁移
python scripts/migrate.py

# 测试
pytest tests/unit tests/integration -q
```

健康检查：``curl http://127.0.0.1:8000/api/health``

## 维护注意点

- **设计约束**：Sprint 0 不引入 ``pydantic-settings`` 依赖，``Settings`` 用显式字段 + ``load()`` 类方法实现。
- **迁移幂等**：``_migrations`` 表追踪已执行脚本；重复运行 ``apply_migrations`` 不会重复执行 DDL。
- **表数口径**：迁移后 ``count_tables`` 返回 29（含 ``_migrations``），业务表 = 28；``/api/health`` 端点已减去 1。
- **包导入**：脚本入口通过 ``sys.path.insert(0, REPO_ROOT)`` 让 ``python scripts/xxx.py`` 可直接解析 ``packages.*``。
- **路由前缀**：业务路由全部挂在 ``/api`` 前缀下，与 vite dev proxy 配合；``create_app`` 统一挂载，
  router 内不要再加 ``/api`` 前缀。
- **ID 与时间戳**：所有 Service 必须用 ``new_id("<prefix>")`` 与 ``now_iso()``，禁止业务层直接构造主键或时间戳。
- **权威文档**：``docs/impl/IMPLEMENTATION-PLAN-v0.md`` §1 决策 §2 Sprint 表；``docs/state-model/state-delta-v0.md``（后续 Sprint 参考）。