"""slug 派生与冲突去重（book-slug / genre-pack-slug 共用同一套归一化）。

规则（确定性，便于跨平台、跨次重跑结果一致）：

1. 输入 = ``projects.name``（书）或 ``genre_packs.name``（题材包）；trim 首尾空白。
2. 全部 Unicode 字符做 NFKD 归一化 → 把组合字符拆开（如 ``é`` → ``e`` + 组合符）。
3. 逐字符保留 ``[a-z0-9]+`` 命中；遇到空格或下划线替换为 ``-``；
   其它字符（含中文）替换为 ``-``。
4. 连续 ``-`` 折叠成单个；首尾 ``-`` 删除；整体 lowercase。
5. 结果为空（纯中文/纯特殊字符）或仅剩 ``-`` 时 → fallback：书走
   ``book-{project_id 末段 8 字符}``，题材包走 ``pack-{pack_id 末段 8 字符}``
   （同样 lowercase + sanitize）。
6. **Windows 保留名兜底**：若 slug 命中 Windows 设备保留名（``CON`` / ``PRN``
   / ``AUX`` / ``NUL`` / ``COM1-9`` / ``LPT1-9`` 等），无论大小写，全部加前缀
   （``book-`` / ``pack-``，见 :func:`derive_slug` 与 :func:`derive_genre_slug`，
   如 ``con`` → ``book-con`` / ``pack-con``），避免 ``mkdir`` 时抛
   ``FileExistsError`` 或在 Windows Explorer 中行为异常。Linux 下同样安全
   （无副作用，目录名照样合法）。
7. 去重：在目标内容仓下扫描现有子目录（书 = 内容仓根，题材包 = ``genres/``），
   遇到同名则追加 ``-2`` / ``-3`` … 直到不冲突为止。

注：第三步对中文字符直接替换为 ``-``，不做拼音 fallback——拼音依赖外部库
或码表，会让「确定性」与「无外部依赖」两条线都失守；按主会话设计意图
「非 ASCII fallback 拼音/缩写」在此版本中以「中文→空 slug→用 id 末段兜底」
处理，结构稳定、无歧义、跨平台一致。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

__all__ = ["derive_genre_slug", "derive_slug", "unique_slug"]


_NON_SLUG_CHAR = re.compile(r"[^a-z0-9-]+")
_MULTI_DASH = re.compile(r"-+")

# Windows 设备保留名（DOS 设备文件）。大小写不敏感；含 ``CON`` / ``PRN`` /
# ``AUX`` / ``NUL`` / ``COM1`` ~ ``COM9`` / ``LPT1`` ~ ``LPT9``。
# 落到 Windows 文件系统时这些名字会被解析为设备而非普通文件，导致
# ``open()`` / ``mkdir()`` 行为异常。slug 派生阶段统一前缀化规避。
_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def _normalize_to_ascii(text: str) -> str:
    """NFKD 归一化 + 丢组合字符 → ASCII 候选。"""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _escape_reserved(slug: str, prefix: str = "book") -> str:
    """若 ``slug`` 命中 Windows 设备保留名（大小写不敏感），加 ``prefix-`` 前缀。"""
    if slug.lower() in _WINDOWS_RESERVED_NAMES:
        return f"{prefix}-{slug}"
    return slug


def _slugify(raw: str) -> str:
    """归一化管线：NFKD → lowercase → 非 ``[a-z0-9-]`` 折叠为 ``-`` → 去首尾 ``-``。

    空结果返回空串（由调用方决定 fallback 口径）。
    """
    ascii_text = _normalize_to_ascii(raw)
    slug = ascii_text.lower()
    slug = _NON_SLUG_CHAR.sub("-", slug)
    return _MULTI_DASH.sub("-", slug).strip("-")


def derive_slug(project_name: str | None, project_id: str) -> str:
    """从 project name 派生基础 book slug（不含去重后缀）。

    确定性：相同输入永远给出相同输出。

    参数：
        project_name: ``projects.name``；None 或空串时走 fallback。
        project_id:  ``projects.project_id``；fallback 时取末段 8 字符。

    返回：
        基础 slug（已 lowercase / 已折叠连续 ``-`` / 已去首尾 ``-`` / 已
        对 Windows 保留名前缀化）。
    """
    raw = (project_name or "").strip()
    if not raw:
        return _escape_reserved(_fallback_slug(project_id, "book"), "book")
    base = _slugify(raw) or _fallback_slug(project_id, "book")
    return _escape_reserved(base, "book")


def derive_genre_slug(pack_name: str | None, pack_id: str) -> str:
    """从题材包 name 派生基础 genre slug（不含去重后缀）。

    与 :func:`derive_slug` 同一条归一化管线，仅 fallback 口径不同：

    - ``pack_name`` 能归一化出 ASCII slug（如 ``Male Quick Trans`` →
      ``male-quick-trans``）→ 用 name 的结果；
    - ``pack_name`` 为空 / 纯中文 / 纯特殊字符（归一化后空串）→
      ``pack-<pack_id 末 8 字符>``（题材包 id 常为 ``gp_<12hex>`` 或内容仓
      约定的 ``genre-...-v1``；末 8 字符口径与 book fallback 一致）；
    - Windows 保留名 → 加 ``pack-`` 前缀。

    确定性：相同输入永远给出相同输出。

    注：``pack_id`` 末 8 字符对可读 id（如 ``genre-male-quicktrans-v1``）会得到
    ``pack-trans-v1`` 这类形态——可读性差但确定性 / 去重稳定；service 层
    :meth:`ContentSyncService.push_genre_pack` 对同一 ``pack_id`` 复用既有目录，
    故 slug 只在首次 push 时确定一次。
    """
    raw = (pack_name or "").strip()
    if raw:
        base = _slugify(raw)
        if base:
            return _escape_reserved(base, "pack")
    return _escape_reserved(_fallback_slug(pack_id, "pack"), "pack")


def _fallback_slug(pack_or_project_id: str, prefix: str) -> str:
    """name 全为非 ASCII 时的 fallback slug：``<prefix>-<id 末 8 字符>``。"""
    tail = (pack_or_project_id or "").strip()
    # 取末段 8 字符：id 通常形如 ``prj_abc12345`` / ``gp_ab12cd34``，末段取后半段哈希更稳定。
    tail = tail[-8:] if len(tail) > 8 else tail
    tail = _NON_SLUG_CHAR.sub("", tail.lower())
    return f"{prefix}-{tail}" if tail else prefix


def unique_slug(base: str, content_dir: Path) -> str:
    """在 ``content_dir`` 下扫描现有 book 子目录，给 ``base`` 追加去重后缀。

    实现：只读 ``content_dir.iterdir()``，不创建任何文件；返回的 slug 若
    等于 ``base``，说明 ``base`` 当前未冲突。
    """
    if not content_dir.exists():
        return base
    existing = {p.name for p in content_dir.iterdir() if p.is_dir()}
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"
