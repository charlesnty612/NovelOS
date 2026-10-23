"""``scripts/mutation_harness.py`` 的 21 条硬编码变异的**只读预检看守**（dev-b 批次二十）。

为什么补这条
------------
仓里有**两套**突变执行器（AGENTS 坑区已登记「登记不修（批次十八）：两套突变执行器
并存」）：``tests/mutation_specs/run_spec.py``（360 条规格，**有**只读预检
``validate_specs.py``）与 ``scripts/mutation_harness.py``（**21 条硬编码**，
**无任何预检**）。

后果不是「21 条没跑过」，而是**锚点被别人的改动打断时无人预警**：
执行器跑起来会判 ``[ABORT]``（锚点命中 0 次或 >1 次），但那已经是**跑的时候**
才响 ⇒ 锚点漂移会一路静默到某天有人碰巧跑那个脚本为止。
⇒ 本文件把「21 条的锚点在**当前树**里恰好命中 1 次、目标文件与定向用例文件都存在、
替换不是 anchor 的超集（否则等于没改）、.py 目标变异后仍可解析」这几件事
变成**常规跑测就会响**的断言。

⚠️ **为什么用 AST 而不是 import**：``scripts/mutation_harness.py`` 在**模块级**调了
``sys.stdout.reconfigure(...)``（:53）⇒ pytest 捕获期的 ``sys.stdout`` 没有
``reconfigure`` 方法 ⇒ import 即崩（AGENTS 坑区「模块级 ``sys.stdout.reconfigure``
⇒ pytest 里 import 就炸」）。
⇒ 本文件**不 import 它**，而是用 ``ast`` 解析出 ``MUTATIONS`` 的字面量
（``ast.literal_eval``）—— **只读、零副作用**。

零真实 LLM、零内容仓接触、零 dev 库：本文件**只读仓库文件**（不 stat 之外读写，
不改一个字节）。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "scripts" / "mutation_harness.py"
WEB_ROOT = REPO_ROOT / "apps" / "web"

#: 硬编码变异的条数（批次三 7 条 → 批次四 13 条 → 批次五 21 条，2026-10-15）。
EXPECTED_MUTATION_COUNT = 21


def _load_mutations() -> list[tuple[str, str, str, str, str]]:
    """用 AST 取出 ``MUTATIONS`` 字面量（**不执行模块** ⇒ 零 import 副作用）。"""
    tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "MUTATIONS":
            return list(ast.literal_eval(node.value))
    raise AssertionError("scripts/mutation_harness.py 里找不到 MUTATIONS 字面量")


MUTATIONS = _load_mutations()


def _target_path(rel: str) -> Path:
    return REPO_ROOT / rel


def _case_path(test_file: str) -> Path:
    """定向用例文件的绝对路径：``src/...`` 属前端（在 ``apps/web/`` 下），
    ``tests/...`` 属后端（在仓库根下）。"""
    if test_file.startswith("src/"):
        return WEB_ROOT / test_file
    return REPO_ROOT / test_file


def test_mutation_count_is_pinned():
    """条数钉住：21 条。增删硬编码变异必须同批更新这里。"""
    assert len(MUTATIONS) == EXPECTED_MUTATION_COUNT


def test_every_entry_has_the_expected_shape():
    """每条是 5 元组：``(编号, 目标文件, 锚点, 替换, 定向用例)``。

    ⚠️ **「替换」允许是空串**：那是**合法的变异**（M13 撤掉 URL 契约登记用的就是
    「把这一行删掉」）⇒ 空替换**不是**「没改」。真正要挡的是
    「replacement 是 anchor 的超集」——那一条由
    :func:`test_every_replacement_actually_changes_the_target` 单独判。
    """
    for entry in MUTATIONS:
        assert isinstance(entry, tuple) and len(entry) == 5, f"形态不对：{entry!r}"
        mid, rel, anchor, repl, case = entry
        for field, name in ((mid, "编号"), (rel, "目标文件"), (anchor, "锚点"), (case, "用例")):
            assert isinstance(field, str) and field, f"{name} 必须是非空字符串：{entry!r}"
        assert isinstance(repl, str), f"替换必须是字符串（可为空串）：{entry!r}"


def test_mutation_ids_are_unique_and_sequential():
    """编号唯一且连续（``M1``…``M21``）⇒ 报告里能按编号定位，不会两条同名。"""
    ids = [m[0] for m in MUTATIONS]
    assert len(set(ids)) == len(ids), f"编号重复：{ids}"
    assert ids == [f"M{i}" for i in range(1, EXPECTED_MUTATION_COUNT + 1)], (
        f"编号应连续 M1..M{EXPECTED_MUTATION_COUNT}，实得 {ids}"
    )


def test_every_target_file_exists():
    """每条的目标文件都必须存在（否则那条变异永远跑不了）。"""
    missing = [m[1] for m in MUTATIONS if not _target_path(m[1]).is_file()]
    assert not missing, f"目标文件不存在：{missing}"


def test_every_anchor_hits_exactly_once():
    """**每条锚点在目标文件里恰好命中 1 次**——这是执行器自己判 ``[ABORT]`` 的那条。

    ⚠️ 这正是「21 条无预检」的那个缺口：执行器**跑的时候**才响，
    而锚点漂移（别人的改动打散了同款声明）会一路静默。
    """
    problems: list[str] = []
    for mid, rel, anchor, _repl, _case in MUTATIONS:
        src = _target_path(rel).read_text(encoding="utf-8")
        hits = src.count(anchor)
        if hits != 1:
            problems.append(f"{mid}: 锚点命中 {hits} 次（要求恰好 1）→ {rel}")
    assert not problems, "锚点漂移（执行器会 [ABORT] 但平时无人预警）：\n" + "\n".join(problems)


def test_every_replacement_actually_changes_the_target():
    """替换必须**真的改变内容**——replacement 是 anchor 的超集就等于「什么都没改」。

    同款教训：批次五 M15 首版写成「在锚点上方插一行注释」⇒ 实测报 SURVIVED
    （量具没量到目标却给出了「存活」结论）。这类空变异必须在这里被挡住。
    """
    problems: list[str] = []
    for mid, rel, anchor, repl, _case in MUTATIONS:
        src = _target_path(rel).read_text(encoding="utf-8")
        mutated = src.replace(anchor, repl, 1)
        if mutated == src:
            problems.append(f"{mid}: 变异未改变内容（replacement 是 anchor 的超集）")
    assert not problems, "\n".join(problems)




def test_every_directed_case_file_exists():
    """每条的**定向用例文件**都必须存在。

    少了它：那条变异会「跑了但没跑用例」⇒ 报出来的是「没红」而不是「该红的没红」
    （同「突变被 SKIP ≠ 突变存活」）。
    """
    missing = [m[4] for m in MUTATIONS if not _case_path(m[4]).is_file()]
    assert not missing, f"定向用例文件不存在：{missing}"
