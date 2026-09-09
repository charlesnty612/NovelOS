"""内容仓路径常量与 Manifest 数据结构。

约定（与上层 PRD 同步器章节保持一致）::

    <content-dir>/<book-slug>/
        novel/
            全书-<slug>.txt            # 整书正文（复用 build_txt kind=book）
            第<N>卷-<title>.txt        # 按卷合并（若该书有 volumes）
            第<N>章-<title>.txt        # 每章正文（复用 build_txt kind=chapter）
        canon/
            characters.json
            world_rules.json
            plot_events.json
            timeline_events.json
            volumes.json
        story_state/
            <state_version>.json       # 每版本 story_states 快照
        backup/
            <slug>-backup-<YYYYmmdd-HHMMSS>.json
        manifest.json

设计要点：

- 默认内容仓路径使用正斜杠形式的 ``D:/zcodeproject/NovelOS-Content``，与
  Windows path 一致；调用方在使用时通过 :class:`pathlib.Path` 转成原生
  形式（避免硬编码 ``/d/`` 风格的反斜杠字符串）。
- 子目录中文名 ``novel / canon / story_state / backup`` 固定，文件名前缀
  （如 ``全书-`` / ``第N章-``）按可读性优先中文；CLI 读取时按这些名字匹配，
  不依赖遍历顺序。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "BOOK_DIRS",
    "BOOK_SLUG_HINT",
    "DEFAULT_CONTENT_DIR",
    "Manifest",
    "manifest_path",
]


# 默认内容仓根目录。与软件仓 ``D:/zcodeproject/NovelOS`` 同盘，独立 git 仓。
DEFAULT_CONTENT_DIR: Path = Path("D:/zcodeproject/NovelOS-Content")

# 一本书的固定子目录清单（写顺序=目录创建顺序）。
BOOK_DIRS: tuple[str, ...] = (
    "novel",
    "canon",
    "story_state",
    "backup",
)

# 仅提示性变量：manifest 中 ``slug_hint`` 字段在内容仓目录下展示用的名称前缀，
# 不影响实际写入。
BOOK_SLUG_HINT = "book"


# 章节 / 卷 txt 文件名中的非法字符（Windows 文件名限制 + 排版美观）。
# 仅剔除 Windows 文件名禁用字符 ``\ / : * ? " < > |``，不引入 ``\r \n \t``
# 字面量——这两个字面量若不带 ``r`` 前缀的 raw 串会被 Python 解析为控制字符，
# 在正则字符类里反而把字面 ``r / n / t`` 当作可匹配项踩坑。
_FILENAME_SANITIZE = re.compile(r'[\\/:*?"<>|]+')
_FILENAME_COLLAPSE = re.compile(r"\s+")


def _safe_basename(name: str | None, fallback: str) -> str:
    """把标题清洗成可作为 Windows 文件名的 basename。

    - 去前后空白；
    - ``\\ / : * ? " < > |`` 替换为空串；
    - 内部空白折叠为单空格；
    - 结果为空则返回 fallback。
    """
    if not name:
        return fallback
    s = str(name).strip()
    s = _FILENAME_SANITIZE.sub("", s)
    s = _FILENAME_COLLAPSE.sub(" ", s).strip()
    return s or fallback


def novel_book_filename(slug: str) -> str:
    """整书 txt 文件名：``全书-<slug>.txt``。"""
    return f"全书-{_safe_basename(slug, 'untitled')}.txt"


def novel_chapter_filename(number: int, title: str | None) -> str:
    """单章 txt 文件名：``第<N>章-<title>.txt``；无标题时仅保留章节前缀。"""
    safe_title = _safe_basename(title, "")
    suffix = f"-{safe_title}" if safe_title else ""
    return f"第{number}章{suffix}.txt"


def novel_volume_filename(number: int, title: str | None) -> str:
    """单卷合并 txt 文件名：``第<N>卷-<title>.txt``。"""
    safe_title = _safe_basename(title, "")
    suffix = f"-{safe_title}" if safe_title else ""
    return f"第{number}卷{suffix}.txt"


def backup_filename(slug: str, timestamp_compact: str) -> str:
    """JSON 备份包文件名：``<slug>-backup-<YYYYmmdd-HHMMSS>.json``。

    ``timestamp_compact`` 由调用方按 ``datetime.now().strftime("%Y%m%d-%H%M%S")``
    格式提供。
    """
    return f"{_safe_basename(slug, 'untitled')}-backup-{timestamp_compact}.json"


def story_state_filename(state_version: int) -> str:
    """按 state_version 命名：``<N>.json``（state_version 本身单调递增，按此命名
    可天然去重，重跑 push 不重复写）。"""
    return f"{int(state_version)}.json"


def manifest_path(book_dir: Path) -> Path:
    """manifest.json 在书目录里的固定位置。"""
    return book_dir / "manifest.json"


@dataclass
class Manifest:
    """``manifest.json`` 反序列化形态。

    字段按字段名序列化为 JSON（保持顺序便于阅读），新增字段时务必保持
    老字段不变以保证向下兼容。
    """

    project_id: str
    name: str
    slug: str
    genre: str | None
    status: str | None
    target_words: int | None
    synced_at: str
    chapter_count: int
    draft_count: int
    story_state_versions: list[int] = field(default_factory=list)
    content_dir: str | None = None  # 冗余写，便于离线查看内容仓时定位软件仓

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        """从 dict 反序列化（容错：缺失字段走默认）。

        - 所有必需字段都用 ``data.get(...)`` + 默认值兜底，缺字段不抛错；
        - 类型异常字段（如 ``chapter_count`` 传字符串）尝试 ``int(...)`` 转
          换，转换失败 fallback 默认值，避免外部篡改的 manifest 把
          ``list_books`` / 后续读路径整链路带挂；
        - ``content_dir`` / ``genre`` / ``status`` 这类可空字段缺失或为
          ``None`` 时保持 ``None``。
        """
        if not isinstance(data, dict):
            return cls(
                project_id="",
                name="",
                slug="",
                genre=None,
                status=None,
                target_words=None,
                synced_at="",
                chapter_count=0,
                draft_count=0,
                story_state_versions=[],
                content_dir=None,
            )

        def _opt_int(v: Any) -> int | None:
            """可空整数：None → None；可转换 → int；其它 → None（不抛错）。"""
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        def _opt_str(v: Any) -> str | None:
            """可空字符串：None → None；其它强制 str。"""
            if v is None:
                return None
            try:
                return str(v)
            except Exception:  # noqa: BLE001
                return None

        def _opt_list_int(v: Any) -> list[int]:
            """可空版本列表：能转 int 的留下，不能的丢弃（避免下游类型错）。"""
            if not isinstance(v, list):
                return []
            out: list[int] = []
            for item in v:
                try:
                    out.append(int(item))
                except (TypeError, ValueError):
                    continue
            return out

        return cls(
            project_id=str(data.get("project_id", "") or ""),
            name=str(data.get("name", "") or ""),
            slug=str(data.get("slug", "") or ""),
            genre=_opt_str(data.get("genre")),
            status=_opt_str(data.get("status")),
            target_words=_opt_int(data.get("target_words")),
            synced_at=str(data.get("synced_at", "") or ""),
            chapter_count=int(data.get("chapter_count", 0) or 0),
            draft_count=int(data.get("draft_count", 0) or 0),
            story_state_versions=_opt_list_int(data.get("story_state_versions")),
            content_dir=_opt_str(data.get("content_dir")),
        )
