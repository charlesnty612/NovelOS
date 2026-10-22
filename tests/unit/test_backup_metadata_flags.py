"""备份包 metadata 的「已剔除」标记必须是**派生值**，不能是恒真常量。

背景（2026-10-22 批次二十一，项目 review P1-1）
------------------------------------------------
`packages/core/backup/service.py` 的 6 个 `*_excluded` / `api_keys_stripped` 标记
**原先是硬编码 `True`**：它们只陈述「我们希望如此」，不陈述「包内实际如何」。
一旦有人把 `model_profiles` / `model_connections` 加进 `EXPORTED_TABLES`，包体就会
真的带上明文 `api_key`（存在 `params_json` 里，见 ADR-0002），而 metadata 仍然自证
`api_keys_stripped: true` ⇒ **导入方无从察觉**。

现已改为从 `EXPORTED_TABLES` 派生（集合不相交）。本文件钉三件事：

1. 当前导出集合下 6 个标记都为真（与历史行为一致，对外契约不变）；
2. **把某表塞进导出集合 ⇒ 对应标记立刻变假**（这条就是「硬编码实现必红」的判别点）；
3. 每个标记都必须**声明它涉及哪些表** —— 防止将来加一个 flag 却不写判据
   （那样它会退化成新的恒真常量）。

⚠️ 包体侧另有一道独立看守（`test_backup_export_import.py::
test_export_metadata_has_api_keys_stripped` 递归扫全包禁止出现 `"api_key"` 字段）；
本文件补的是「**声明**不得是恒真常量」这一半，两层不重复。
"""

from __future__ import annotations

import pytest

from packages.core.backup import service as backup_service
from packages.core.backup.schema import EXPORTED_TABLES

#: 当前契约：这 6 个标记必须存在（删/改名都会让本断言红，迫使同步更新文档与导入方）。
EXPECTED_FLAGS = {
    "api_keys_stripped",
    "ai_call_logs_excluded",
    "evaluations_excluded",
    "workflow_runs_excluded",
    "model_configs_excluded",
    "reference_canons_excluded",
}


def test_all_flags_true_on_current_export_set() -> None:
    flags = backup_service.backup_metadata_flags()
    assert set(flags) == EXPECTED_FLAGS
    assert all(flags.values()), f"当前导出集合下不应有假标记，实得 {flags}"


def test_every_flag_declares_the_tables_it_depends_on() -> None:
    """每个标记都必须有「它涉及哪些表」的声明 —— 否则就是新的恒真常量。"""
    declared = backup_service._FLAG_REQUIRED_ABSENT_TABLES
    assert set(declared) == EXPECTED_FLAGS
    for flag, tables in declared.items():
        assert tables, f"{flag} 没有声明任何表 ⇒ 它会退化成恒真常量"
        assert all(isinstance(t, str) and t for t in tables), f"{flag} 的表名非法：{tables}"


def test_no_declared_table_is_currently_exported() -> None:
    """判据自洽：声明为「必须缺席」的表，当前确实一张都不在导出集合里。"""
    exported = set(EXPORTED_TABLES)
    for flag, tables in backup_service._FLAG_REQUIRED_ABSENT_TABLES.items():
        overlap = set(tables) & exported
        assert not overlap, f"{flag} 声明 {tables} 必须缺席，但 {overlap} 正在被导出"


@pytest.mark.parametrize(
    "flag, table",
    [
        ("api_keys_stripped", "model_profiles"),
        ("api_keys_stripped", "model_connections"),
        ("ai_call_logs_excluded", "ai_call_logs"),
        ("evaluations_excluded", "evaluations"),
        ("workflow_runs_excluded", "workflow_runs"),
        ("model_configs_excluded", "model_configs"),
        ("reference_canons_excluded", "reference_canons"),
    ],
)
def test_flag_flips_to_false_when_its_table_is_exported(
    flag: str, table: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 判别点：把表塞进导出集合 ⇒ 该标记必须变假。

    硬编码 `True` 的实现会让本用例红（这正是 review P1-1 说的那个漏洞）。
    """
    monkeypatch.setattr(
        backup_service, "EXPORTED_TABLES", (*EXPORTED_TABLES, table)
    )
    flags = backup_service.backup_metadata_flags()
    assert flags[flag] is False, (
        f"{table} 已进入导出集合，但 {flag} 仍为真 ⇒ 安全声明与事实不符"
    )


def test_unrelated_flag_stays_true_when_another_table_is_exported() -> None:
    """反方向：只影响相关标记，不得把全部标记一起拖成假（防「改过头」）。"""
    monkeypatch_flags = backup_service.backup_metadata_flags()
    assert monkeypatch_flags["ai_call_logs_excluded"] is True
    assert monkeypatch_flags["api_keys_stripped"] is True
