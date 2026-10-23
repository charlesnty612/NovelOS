"""跨测试域共用的「**每例新库**」夹具（2026-10-23 task-10 第一刀）。

## 为什么抽它

各测试域里到处是同一段「建一个临时库并把迁移跑一遍」的样板，逐字重复：

```python
settings = Settings(data_dir=tmp_path, log_level="WARNING")
apply_migrations(settings.db_path)
```

本模块把这段收成**一处**定义，各测试文件调用它即可。

## 硬约束（与 task-10 板面一致）

- **仍是每例新库**：``tmp_path`` 由 pytest 按用例分配 ⇒ 每个用例一份独立库，
  **不共享、不 session 级复用**（本仓历史事故族：跨用例状态污染）。
  本模块只做「建库 + 跑迁移」，**不写任何业务数据** ⇒ 数据种子仍由各域自己负责。
- **不改语义**：本模块只替代「建库」这一段；迁移目录、库文件名、返回值类型
  都由调用点原样传入 ⇒ 断言 / 用例名 / fixture 作用域一律不动。

## 参数留出的扩展位

``db_name`` 与 ``migrations_dir`` 让**带显式迁移目录**的调用点将来也能直接复用本
helper（``apply_migrations`` 的第二参默认 ``None`` ⇒ 走仓内默认迁移目录，见
``packages/core/db.py:156``）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations


def migrated_db(
    tmp_path: Path,
    *,
    db_name: str = "novelos.db",
    migrations_dir: Path | str | None = None,
) -> Path:
    """在 ``tmp_path`` 下建一个**新库**并跑完迁移，返回该库的路径。

    参数
    ----
    tmp_path:
        pytest 的用例级临时目录（每例独有 ⇒ 库天然隔离）。
    db_name:
        库文件名。默认 ``"novelos.db"`` = ``Settings`` 由 ``data_dir`` 推导出的
        名字（``packages/core/config.py:43-46``）⇒ 与被替代的样板逐位同名。
    migrations_dir:
        显式迁移目录；``None`` = 走 ``apply_migrations`` 的仓内默认目录。

    返回
    ----
    ``Path``：库文件路径。调用点若原先返回 ``str``，自行 ``str(...)`` 转换即可
    （本仓 ``tests/unit/quality`` 两种都有，故 helper 返回最基础的 ``Path``）。

    ⚠️ 与被替代的样板**逐位等价**：
    ``Settings(data_dir=tmp_path, log_level="WARNING").db_path``
    == ``Path(tmp_path) / "novelos.db"``（``config.py`` 里 ``db_path is None``
    ⇒ ``data_dir / "novelos.db"``）⇒ 默认参数下的库路径字符串与旧样板完全相同。
    """
    settings = Settings(
        data_dir=tmp_path,
        db_path=Path(tmp_path) / db_name,
        log_level="WARNING",
    )
    # migrations_dir=None ⇒ apply_migrations 走仓内默认迁移目录（db.py:165-166）
    apply_migrations(settings.db_path, migrations_dir)
    return settings.db_path
