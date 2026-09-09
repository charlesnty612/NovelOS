"""content_sync.slug 单元测试（V1.0）。

覆盖：

- 纯 ASCII name：原样保留 + lowercase；
- 含中文 name：非 ASCII 字符替换为 ``-``，剩余 ASCII（如 ``"A"``）保留；
- 全部非 ASCII（纯中文 / 纯 emoji）name：fallback 到 ``book-<id 末 8 字符>``；
- 名称首尾 ``-`` / 连续 ``-`` 折叠；
- ``unique_slug`` 冲突去重（``-2`` / ``-3`` 顺序追加）；
- ``unique_slug`` 不冲突时直接返回 base；
- 确定性：相同输入永远相同输出（多次调用）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.core.content_sync.slug import derive_slug, unique_slug


class TestDeriveSlug:
    def test_pure_ascii_lowercased(self) -> None:
        assert derive_slug("Hello World", "prj_aaaaaaaa") == "hello-world"

    def test_underscore_replaced_with_dash(self) -> None:
        assert derive_slug("smoke_test_1", "prj_aaaaaaaa") == "smoke-test-1"

    def test_consecutive_dashes_collapsed(self) -> None:
        assert derive_slug("a---b", "prj_aaaaaaaa") == "a-b"

    def test_leading_trailing_dashes_stripped(self) -> None:
        assert derive_slug("---abc---", "prj_aaaaaaaa") == "abc"

    def test_chinese_mixed_with_ascii_keeps_ascii(self) -> None:
        # 中文字符被替换为 ``-``，但 ASCII 部分（如末尾的 ``A``）保留。
        assert derive_slug("测试书 A", "prj_aaaaaaaa") == "a"

    def test_pure_chinese_falls_back_to_book_id_tail(self) -> None:
        # 全部非 ASCII → fallback 到 ``book-<id 末 8 字符>``
        assert derive_slug("测试书", "prj_abc12345") == "book-abc12345"

    def test_empty_name_falls_back(self) -> None:
        assert derive_slug("", "prj_abc12345") == "book-abc12345"

    def test_none_name_falls_back(self) -> None:
        assert derive_slug(None, "prj_abc12345") == "book-abc12345"

    def test_short_project_id_uses_full(self) -> None:
        # project_id 不足 8 字符时取整段
        assert derive_slug("中文", "abc1") == "book-abc1"

    def test_nfkd_diacritics_stripped(self) -> None:
        # NFKD 把组合字符拆开，``é`` → ``e``
        assert derive_slug("café", "prj_aaaaaaaa") == "cafe"

    def test_determinism(self) -> None:
        # 多次调用结果一致
        a = derive_slug("测试书 A", "prj_abc12345")
        b = derive_slug("测试书 A", "prj_abc12345")
        assert a == b


class TestUniqueSlug:
    def test_no_conflict_returns_base(self, tmp_path: Path) -> None:
        # content_dir 不存在时直接返回 base，不创建目录
        assert unique_slug("foo", tmp_path / "absent") == "foo"

    def test_empty_dir_returns_base(self, tmp_path: Path) -> None:
        assert unique_slug("foo", tmp_path) == "foo"

    def test_conflict_appends_dash_two(self, tmp_path: Path) -> None:
        (tmp_path / "foo").mkdir()
        assert unique_slug("foo", tmp_path) == "foo-2"

    def test_multiple_conflicts_increment(self, tmp_path: Path) -> None:
        (tmp_path / "foo").mkdir()
        (tmp_path / "foo-2").mkdir()
        (tmp_path / "foo-3").mkdir()
        assert unique_slug("foo", tmp_path) == "foo-4"

    def test_unrelated_dirs_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "bar").mkdir()
        (tmp_path / "baz").mkdir()
        assert unique_slug("foo", tmp_path) == "foo"

    def test_files_in_content_dir_not_counted(self, tmp_path: Path) -> None:
        # content_dir 下存在的「文件」不算 book 目录冲突
        (tmp_path / "README.md").touch()
        assert unique_slug("foo", tmp_path) == "foo"


@pytest.mark.parametrize(
    "name,project_id,expected",
    [
        ("Hello World", "prj_aaaaaaaa", "hello-world"),
        ("", "prj_abc12345", "book-abc12345"),
        ("测试", "prj_abc12345", "book-abc12345"),
        ("A", "prj_abc12345", "a"),
    ],
)
def test_derive_slug_parametrized(name: str, project_id: str, expected: str) -> None:
    assert derive_slug(name, project_id) == expected
