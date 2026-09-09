"""book-slug 派生与冲突去重。

规则（确定性，便于跨平台、跨次重跑结果一致）：

1. 输入 = ``projects.name``；trim 首尾空白。
2. 全部 Unicode 字符做 NFKD 归一化 → 把组合字符拆开（如 ``é`` → ``e`` + 组合符）。
3. 逐字符保留 ``[a-z0-9]+`` 命中；遇到空格或下划线替换为 ``-``；
   其它字符（含中文）替换为 ``-``。
4. 连续 ``-`` 折叠成单个；首尾 ``-`` 删除；整体 lowercase。
5. 结果为空（纯中文/纯特殊字符）或仅剩 ``-`` 时 → fallback 到
   ``book-{project_id 末段 8 字符}``（同样 lowercase + sanitize）。
6. **Windows 保留名兜底**：若 slug 命中 Windows 设备保留名（``CON`` / ``PRN``
   / ``AUX`` / ``NUL`` / ``COM1-9`` / ``LPT1-9`` 等），无论大小写，全部加
   ``book-`` 前缀（如 ``con`` → ``book-con``），避免 ``mkdir`` 时抛
   ``FileExistsError`` 或在 Windows Explorer 中行为异常。Linux 下同样安全
   （无副作用，目录名照样合法）。
7. 去重：在目标内容仓下扫描现有 book 子目录，遇到同名则追加 ``-2`` / ``-3``
   … 直到不冲突为止。

注：第三步对中文字符直接替换为 ``-``，不做拼音 fallback——拼音依赖外部库
或码表，会让「确定性」与「无外部依赖」两条线都失守；按主会话设计意图
「非 ASCII fallback 拼音/缩写」在此版本中以「中文→空 slug→用 project_id
末段兜底」处理，结构稳定、无歧义、跨平台一致。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

__all__ = ["derive_slug", "unique_slug"]


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


def _escape_reserved(slug: str) -> str:
    """若 ``slug`` 命中 Windows 设备保留名（大小写不敏感），加 ``book-`` 前缀。"""
    if slug.lower() in _WINDOWS_RESERVED_NAMES:
        return f"book-{slug}"
    return slug


def derive_slug(project_name: str | None, project_id: str) -> str:
    """从 project name 派生基础 slug（不含去重后缀）。

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
        return _escape_reserved(_fallback_slug(project_id))
    ascii_text = _normalize_to_ascii(raw)
    slug = ascii_text.lower()
    # 非 [a-z0-9-] 全部折叠为 ``-``
    slug = _NON_SLUG_CHAR.sub("-", slug)
    slug = _MULTI_DASH.sub("-", slug).strip("-")
    base = slug or _fallback_slug(project_id)
    return _escape_reserved(base)


def _fallback_slug(project_id: str) -> str:
    """name 全为非 ASCII 时的 fallback slug：``book-<id 末 8 字符>``。"""
    tail = (project_id or "").strip()
    # 取末段 8 字符：project_id 通常形如 ``prj_abc12345``，末段取后半段哈希更稳定。
    tail = tail[-8:] if len(tail) > 8 else tail
    tail = _NON_SLUG_CHAR.sub("", tail.lower())
    return f"book-{tail}" if tail else "book"


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
