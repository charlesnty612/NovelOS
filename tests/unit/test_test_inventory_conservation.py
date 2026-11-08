"""用例清单守恒闸门自己的看守（2026-11-08 批次三十八 · INV）。

本文件守的是 ``scripts/check_test_inventory.py`` 的三件事：

1. **清单与快照一致**——任何人删/加用例都必须显式 ``--update --reason`` 留痕；
2. **快照覆盖面不缩小**——磁盘上的每个测试文件都必须在快照里（防止「把文件挪走
   然后再也不进闸门」）；
3. **量具不瞎**——收集器必须真的看见用例（总数 > 0、已知文件 > 0），
   否则「一致」只是两边都是 0 的假绿。

为什么需要这条闸门：重构批次的第一验收口径是「用例数零增减」，此前全靠手写记账
（``tests/workflow/test_chapter_pipelines.py`` 顶部「拆分前 17 ⇒ 10 + 7 = 17」就是人工数的）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_test_inventory.py"


def _load_script():
    """把脚手架当模块加载（scripts/ 不是包）。"""
    spec = importlib.util.spec_from_file_location("check_test_inventory", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_test_inventory"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_inventory_matches_snapshot():
    """当前用例清单必须与快照逐文件一致。"""
    mod = _load_script()
    current = mod.collect_inventory()
    snapshot = mod.load_snapshot()
    problems = mod.diff_inventory(current, snapshot)
    assert problems == [], (
        "用例清单与快照不一致（重构必须零增减；有意变更请跑 --update --reason）：\n  "
        + "\n  ".join(problems)
    )


def test_snapshot_covers_every_test_file_on_disk():
    """反向不变量：磁盘上的每个测试文件都必须在快照里（覆盖面不得静默缩小）。"""
    mod = _load_script()
    mod.load_snapshot()
    current = mod.collect_inventory()
    for side in ("python", "frontend"):
        for path in current[side]["files"]:
            assert path in mod.load_snapshot()[side]["files"], path


def test_collector_is_not_blind():
    """量具自检：收集器必须看得见用例，且能看见**具体**文件。

    没有这条，上面两条在「两边都是 0」时会一起假绿。
    """
    mod = _load_script()
    current = mod.collect_inventory()
    assert current["python"]["total"] > 0
    assert current["frontend"]["total"] > 0
    assert current["python"]["files"].get("tests/unit/test_test_inventory_conservation.py", 0) >= 3
    # 解析失败（-1）不得存在——它会让总数静默变小
    for side in ("python", "frontend"):
        for path, n in current[side]["files"].items():
            assert n >= 0, f"{path} 解析失败，清单不可判"
