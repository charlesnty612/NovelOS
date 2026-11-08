"""文件体量看守：1500 行硬规则机器化（2026-11-08 批次三十八 · GOV-SIZE）。

## 为什么

AGENTS.md §二 硬规则 1 写着「新增模块不得超 1500 行；>1500 行的既有文件拆多模块 + 门面」，
但**此前没有任何看守**——规则只以散文存在。后果是两次「清零」之后又长出超标文件，
且事后新增的清单还漏登了 ``engine.py`` 与 ``project_init/pipeline.py``。

## 口径

- 扫描 ``packages/**/*.py`` 与 ``apps/web/src/**/*.ts(x)``；
- ``*.generated.ts`` 是 codegen 产物，不适用（改动它没有意义）；
- 超限的存量文件登进 **只减债账**（``_DEBT``）：数值是**上限**，只许降不许升；
  一旦降到 1500 以内，**必须从债账里删掉**（否则「豁免」会变成永久豁免）。

## 判别

- 新增一个 1501 行的文件 ⇒ 第一条红；
- 把债账里的文件再多写一行 ⇒ 第二条红；
- 债账文件拆到 1500 以内却不删条目 ⇒ 第三条红。
"""

from __future__ import annotations

from pathlib import Path

_LIMIT = 1500
_ROOT = Path(__file__).resolve().parents[2]

#: 存量债账（**只减不增**）。值 = 建档时的行数上限；降到 _LIMIT 以内必须删条目。
_DEBT: dict[str, int] = {
    "apps/web/src/pages/ChapterDetailPage.test.tsx": 3895,
    "apps/web/src/api/types.ts": 1912,
    "packages/core/workflow_runtime/engine.py": 1698,
    "packages/workflows/project_init/pipeline.py": 1558,
}


def _iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    files.extend((_ROOT / "packages").rglob("*.py"))
    web_src = _ROOT / "apps" / "web" / "src"
    for suffix in ("*.ts", "*.tsx"):
        files.extend(web_src.rglob(suffix))
    return [
        p
        for p in files
        if p.is_file() and not p.name.endswith(".generated.ts")
    ]


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8-sig").splitlines())


def _rel(path: Path) -> str:
    return path.relative_to(_ROOT).as_posix()


def test_no_new_oversized_files():
    """未登记在债账里的文件一律不得超过 1500 行。"""
    offenders = []
    for path in _iter_scanned_files():
        rel = _rel(path)
        if rel in _DEBT:
            continue
        n = _line_count(path)
        if n > _LIMIT:
            offenders.append(f"{rel}: {n} 行")
    assert not offenders, (
        f"以下文件超过 {_LIMIT} 行且未登记债账（AGENTS §二 硬规则 1）：\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_debt_entries_do_not_grow():
    """债账里的文件只能变短：当前行数不得超过登记值（只减不增）。"""
    grew = []
    for rel, budget in _DEBT.items():
        path = _ROOT / rel
        assert path.is_file(), f"债账里的文件不存在：{rel}（请删掉该条目）"
        n = _line_count(path)
        if n > budget:
            grew.append(f"{rel}: {budget} → {n}（+{n - budget}）")
    assert not grew, "债账文件变长了（只减不增）：\n  " + "\n  ".join(sorted(grew))


def test_debt_entries_must_be_removed_once_under_limit():
    """债账条目一旦降到 1500 行以内，必须从债账删除（禁止永久豁免）。"""
    stale = []
    for rel in _DEBT:
        path = _ROOT / rel
        if path.is_file() and _line_count(path) <= _LIMIT:
            stale.append(f"{rel}: 已降到 {_line_count(path)} 行，请从 _DEBT 删掉该条目")
    assert not stale, "\n".join(stale)


def test_scanner_is_not_blind():
    """量具自检：扫描面必须真的扫到大量文件（否则「无超标」是假绿）。"""
    files = _iter_scanned_files()
    assert len(files) > 200, f"只扫到 {len(files)} 个文件，扫描面异常"
    # 已知存在的超大文件必须被扫到
    scanned = {_rel(p) for p in files}
    for rel in _DEBT:
        assert rel in scanned, f"债账里的 {rel} 未被扫描面覆盖"
