# packages/core/workflow_registry（工作流注册表）

## 职责

提供 core 侧的工作流惰性注册与查询能力，业务流程包通过注册 builder 暴露工作流，core 侧按名称读取或遍历工作流定义。

## 对外接口

- `register_workflow(name, builder) -> None`：按非空名称注册可调用 builder；同名注册会覆盖旧 builder，并清除该名称的构建缓存。
- `get_workflow(name) -> dict | None`：首次访问时调用 builder，返回包含 `name`、`nodes`、`version` 等字段的 workflow dict；未注册返回 `None`，同一名称后续读取命中缓存。
- `all_workflows() -> dict[str, dict]`：返回当前注册项的快照，不暴露内部 builder。
- `_reset_for_tests() -> None`：清空注册表与缓存，仅供测试辅助。

## 设计要点

- `_REGISTRY` 保存 `{name: builder}`，`_CACHE` 保存已构建的 workflow dict；构建采用惰性引用，注册阶段不执行业务 builder。
- 最小构建契约要求 builder 返回 `dict`；若结果缺少 `name`，注册名会补入结果。
- core 模块不依赖业务流程包；业务流程包在自身装配阶段写入注册表，core 业务模块只通过本包查询，以切断 core → workflows 反向依赖。
- `packages/core/api/main.py` 负责通过 `importlib.import_module` 做业务流程插件发现；业务流程模块本身仍保持由业务包主动注册。
- 同一工作流名称应保持唯一，重注册前已有缓存会被主动失效。

## 使用约束

- `builder` 必须是可调用对象，并在被查询时返回 workflow 字典；不要在注册调用处提前执行会产生副作用的构造。
- 依赖方应使用 `get_workflow` 或 `all_workflows` 获取已注册定义，不要直接操作内部 `_REGISTRY` / `_CACHE`。
- 测试需要重置状态时使用 `_reset_for_tests`，并由调用方重新导入或注册业务流程包；注册表模块不负责发现或重载业务流程包。
- 业务工作流应遵循现有 workflow 定义契约，确保返回数据包含 `name`，并按调用方要求提供 `nodes` / `version` 等键。

## 测试位置

- `tests/unit/test_no_core_to_workflows_dep.py`：验证 core 业务模块不直接依赖业务流程包。
- 注册表接口、缓存、惰性构建和重置行为的相关测试位于 `tests/`，按具体模块测试文件执行。
