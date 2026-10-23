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

题材包（题材库 P3a）走独立的 ``genres/`` 命名空间::

    <content-dir>/
        genres/
            <pack-slug>/
                pack.json              # 运行时 payload + 元信息（genre-push 写出）
            manifest.json ?            # 不写：pack 清单在内容仓根 manifest.json 的 genres 段
        manifest.json                  # 内容仓根清单：genres 段 = {synced_at, packs[]}
        _quarantine/                   # 隔离区（2026-10-23 新增，concern 4）
            <book-slug>/
                第1章-旧标题.txt        # 判定不了归属的旧正文 —— **移动**到这儿，**不删除**
        <book-slug>/
            ...                        # 书目录结构同上

隔离区（``_quarantine/``）的语义：**只进不删**。旧正文清理只有在「能证明是本工具
写的、且此后没被改过」时才物理删除；否则**移动**到这儿，并在书 manifest 的
``quarantined`` 段留痕（文件名 + 原路径 + 原因）。作者可随时取回。

设计要点：

- 默认内容仓路径使用正斜杠形式的 ``D:/zcodeproject/NovelOS-Content``，与
  Windows path 一致；调用方在使用时通过 :class:`pathlib.Path` 转成原生
  形式（避免硬编码 ``/d/`` 风格的反斜杠字符串）。
- 子目录中文名 ``novel / canon / story_state / backup`` 固定，文件名前缀
  （如 ``全书-`` / ``第N章-``）按可读性优先中文；CLI 读取时按这些名字匹配，
  不依赖遍历顺序。
- ``genres/`` 下同一个 pack 目录里既可能有人编辑的 10 维题材资产（编辑面），
  也可能有 ``pack.json``（运行面）；两者是**双轨**而非同一份数据，见
  :mod:`packages.core.content_sync.service` 模块头与 content_sync README。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "BOOK_DIRS",
    "BOOK_SLUG_HINT",
    "CONTENT_MANIFEST_FILENAME",
    "DEFAULT_CONTENT_DIR",
    "GENRES_DIR",
    "GENRE_PACK_FILENAME",
    "QUARANTINE_DIR",
    "GenrePackDoc",
    "Manifest",
    "chapter_number_from_filename",
    "content_manifest_path",
    "genre_pack_path",
    "genres_dir",
    "manifest_path",
    "quarantine_dir",
    "volume_number_from_filename",
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


# 正文文件名里的**章节号 / 卷号**（用于「旧文件残留」判定）。
#
# ⚠️ 口径：**按章节号 + 文件名映射**处理，**不**按内容哈希去重——后者会把
# 「人工改过的正文」误判成重复文件而删掉（内容仓是人的编辑面）。
# 整书 txt（``全书-<slug>.txt``）与任何不匹配这两种前缀的文件**一律不动**。
_CHAPTER_TXT_PATTERN = re.compile(r"^第(\d+)章")
_VOLUME_TXT_PATTERN = re.compile(r"^第(\d+)卷")


def chapter_number_from_filename(filename: str) -> int | None:
    """从正文文件名解析**章号**：「第N章-标题.txt」→ ``N``；不是章文件 → ``None``。"""
    m = _CHAPTER_TXT_PATTERN.match(filename)
    return int(m.group(1)) if m else None


def volume_number_from_filename(filename: str) -> int | None:
    """从卷合并文件名解析**卷号**：「第N卷-标题.txt」→ ``N``；不是卷文件 → ``None``。"""
    m = _VOLUME_TXT_PATTERN.match(filename)
    return int(m.group(1)) if m else None


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


# ---------------------------------------------------------------------------
# 题材包（genres/ 命名空间）
# ---------------------------------------------------------------------------

# 题材包命名空间目录（内容仓根下）。
GENRES_DIR = "genres"

# 单包运行时 payload 文件名（与 10 维编辑面文件并存的「运行面」）。
GENRE_PACK_FILENAME = "pack.json"

# 内容仓根清单文件名（书清单 + ``genres`` 段：同步时间 / pack 清单）。
CONTENT_MANIFEST_FILENAME = "manifest.json"

# 隔离区目录名（内容仓根下，与 ``genres/`` 同级）。
#:
#: **为什么需要它**（2026-10-23 返工 · concern 4）：旧正文清理此前是
#: ``path.unlink()`` ——**无条件物理删除**内容仓里任何形如 ``第N章-*.txt`` 的文件。
#: 但内容仓是**作者的编辑面**：作者手写的正文、作者改过的正文都长这个样子 ⇒
#: 一次「改标题」就可能**删掉作者写的东西**（不可逆）。
#: ⇒ 收窄为：**只有「本工具自己写出且记录在 manifest 里、且此后未被改动」的文件**
#: 才允许删除；**判定不了归属的一律移动到隔离区**（**不删除**），
#: 并在 manifest 留 ``quarantined`` 记录（文件名 + 原路径 + 原因）。
#:
#: 落点选**内容仓根下**（而不是书目录内）：① 一处集中、作者一眼能找到；
#: ② 不污染书目录的固定布局（``novel/ canon/ story_state/ backup/ manifest.json``）。
QUARANTINE_DIR = "_quarantine"


def quarantine_dir(content_dir: Path | str, slug: str) -> Path:
    """``<content-dir>/_quarantine/<book-slug>`` —— 隔离区（**不删除**的文件放这儿）。"""
    return Path(content_dir) / QUARANTINE_DIR / slug


def genres_dir(content_dir: Path | str) -> Path:
    """``<content-dir>/genres``。"""
    return Path(content_dir) / GENRES_DIR


def genre_pack_path(pack_dir: Path | str) -> Path:
    """``<content-dir>/genres/<slug>/pack.json``。"""
    return Path(pack_dir) / GENRE_PACK_FILENAME


def content_manifest_path(content_dir: Path | str) -> Path:
    """``<content-dir>/manifest.json``（内容仓根清单）。"""
    return Path(content_dir) / CONTENT_MANIFEST_FILENAME


@dataclass
class GenrePackDoc:
    """``genres/<slug>/pack.json`` 的反序列化形态。

    语义：**DB 行元信息 + payload** 的落盘封装——payload 是运行面唯一真相
    （软件层只消费它），元信息用于 pull 时定位 / 更新既有 pack 行。

    字段与 :class:`packages.core.genre.model.GenrePack` 对齐；``synced_at``
    是内容仓侧写入时间（审计用，pull 不消费）。
    """

    pack_id: str
    name: str
    genre_tag: str
    version: int
    payload: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    synced_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "GenrePackDoc":
        """容错反序列化（缺字段兜底、类型异常降级，不抛错）。

        必需字段（``pack_id`` / ``payload``）缺失由调用方（pull）校验；
        本函数只保证「形状安全」：payload 非 dict → 空 dict，version 非
        整数 → 0。
        """
        if not isinstance(data, dict):
            return cls(pack_id="", name="", genre_tag="", version=0)

        payload = data.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        try:
            version = int(data.get("version") or 0)
        except (TypeError, ValueError):
            version = 0

        pack_id = data.get("pack_id")
        name = data.get("name")
        genre_tag = data.get("genre_tag")
        source_path = data.get("source_path")
        synced_at = data.get("synced_at")
        return cls(
            pack_id=str(pack_id) if pack_id is not None else "",
            name=str(name) if name is not None else "",
            genre_tag=str(genre_tag) if genre_tag is not None else "",
            version=version,
            payload=payload,
            source_path=str(source_path) if source_path is not None else None,
            synced_at=str(synced_at) if synced_at is not None else "",
        )


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
    story_state_aliases: dict[str, int] = field(default_factory=dict)
    """被合并的快照版本 → 它重复的那个版本（**实际有文件**的版本）。

    2026-10-23 新增：台账写路径会为同一份 ``snapshot_json`` 物化出只换版本号的
    新快照行 ⇒ ``story_state/`` 里只落**一份**（与上一版本内容相同的不重复落盘），
    被合并的版本记在这里 ⇒ **版本信息不丢**。
    """
    novel_files: dict[str, str] = field(default_factory=dict)
    """**归属凭证**：本工具写出过的正文文件名 → 写出时的内容 sha256。

    2026-10-23 新增（concern 4）：旧正文清理此前**无条件 ``unlink``** ⇒ 可能删掉
    作者手写 / 作者改过的正文（内容仓是**编辑面**，不可逆删除是事故）。
    ⇒ 现在只有「**记录在这里** + **当前内容哈希仍一致**」的文件才允许删除；
    判定不了归属的文件一律**移入隔离区**（不删除）。
    """
    quarantined: list[dict[str, str]] = field(default_factory=list)
    """被**移入隔离区**（**未删除**）的文件留痕。

    每条：``{"file", "original_path", "reason", "quarantine_path", "quarantined_at"}``。
    破坏性动作（哪怕只是移动）必须留痕——作者要能查到「我的文件去哪了」。
    """
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

        def _opt_alias_map(v: Any) -> dict[str, int]:
            """可空别名表：``{"被合并版本": "重复的版本"}``（值转 int；不合法则丢弃）。"""
            if not isinstance(v, dict):
                return {}
            out: dict[str, int] = {}
            for key, value in v.items():
                try:
                    out[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
            return out

        def _opt_str_map(v: Any) -> dict[str, str]:
            """可空字符串表（归属凭证 ``novel_files``）：键值都转 str；不合法则丢弃该项。"""
            if not isinstance(v, dict):
                return {}
            out: dict[str, str] = {}
            for key, value in v.items():
                if value is None:
                    continue
                out[str(key)] = str(value)
            return out

        def _opt_records(v: Any) -> list[dict[str, str]]:
            """可空记录列表（隔离区留痕 ``quarantined``）：只留 dict 形态的项，值转 str。"""
            if not isinstance(v, list):
                return []
            out: list[dict[str, str]] = []
            for item in v:
                if not isinstance(item, dict):
                    continue
                out.append({str(k): ("" if val is None else str(val)) for k, val in item.items()})
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
            story_state_aliases=_opt_alias_map(data.get("story_state_aliases")),
            novel_files=_opt_str_map(data.get("novel_files")),
            quarantined=_opt_records(data.get("quarantined")),
            content_dir=_opt_str(data.get("content_dir")),
        )
