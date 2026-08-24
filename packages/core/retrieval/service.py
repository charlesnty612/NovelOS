"""FTS5 召回 service 实现（V2.0 Wave C 任务一）。

零依赖实现：用 Python 标准库 ``sqlite3``（Python 3.11+ 默认编译 FTS5）。
若本机 SQLite 未编译 FTS5，建虚表的迁移 0011 会在创建 chapter_fts 时失败；
运行期不会 silent fallback 到 LIKE 扫描——任务书明确禁止。

设计要点：
- 虚表模式：``CREATE VIRTUAL TABLE chapter_fts USING fts5(chapter_id UNINDEXED,
  content, tokenize='unicode61')``。FTS5 内部存 (chapter_id, content)；
  service 层负责 INSERT OR REPLACE 维护。
- **CJK bigram 索引**：FTS5 unicode61 默认对 CJK 不切词（整句作为一个 token），
  无法命中单字/双字查询。service 在 upsert 时把原文按 CJK 2-gram 切片、用空格
  拼接成 ``indexed_text`` 写入虚表；查询时同样用 bigram 关键词命中。
  ``search()`` 召回时不展示 indexed_text，而是直接按 chapter_id 读
  ``drafts.content`` 取原文、截断到 snippet_max_chars。
- 触发器未使用：external content 模式会引入 trigger 维护成本，且章节正文
  实际存 ``drafts.content`` 而非 ``chapters.content``（一个 chapter 可多个 draft），
  trigger 模型不适用。改为：chapter_commit pipeline 在 commit 成功后调
  :func:`upsert_chapter` 显式 upsert，失败按 summarize 节点相同语义降级
  （log warning，不阻断 commit）。
- 关键词提取（:func:`extract_keywords`）口径：
  - 中文按 2-gram（bigram）切词，相邻 2 字符构成一个 token；保留所有
    出现的 bigram（不去重，避免丢失单字/双字共现信号）。
  - 同时拼接 chapters.plan_json 里 ``character_changes_planned`` 的实体名
    （短串，作为整体 token 进入 FTS5 查询；FTS5 的 unicode61 对整词亦支持）。
  - 英文/数字按空格/标点切词（unicode61 默认行为）。
  - 停用词：去「的」「了」「是」「在」「我」「你」「他」「她」「它」「这」「那」
    「一」「也」「就」「和」「与」「及」共 16 字（中文常见停用词；按 MVP 简化
    集，README 注明后续可替换为 jieba/hanlp 词表）。
- 截断：每段 snippet 截断 ≤ ``_SNIPPET_MAX_CHARS``（默认 300）字符；多余尾部
  省略号「…」标记。
- 章节号标注：每条结果带 ``chapter_id`` / ``chapter_no`` 字段（前者主键、
  后者展示用；preview 端按 chapter_no 排序展示）。

调用面：
- chapter_commit pipeline 节点 ``commit`` 成功后调 :func:`upsert_chapter`；
  失败按 ``summarize`` 节点相同语义降级（log warning）。
- builders.py 装配 director / writer 输入时按章节计划文本调
  :func:`extract_keywords` + :func:`search`。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from packages.core.db import get_connection

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 召回片段截断上限（按字符；中文按 1 字 1 字符计）
_SNIPPET_MAX_CHARS = 300

# 召回 top-N（按 FTS5 bm25 排序）
_RECALL_TOP_N = 3

# 最小召回关键词数：少于该数不发起查询（避免噪声）
_MIN_KEYWORDS = 1

# 关键词去重最大长度：超长 plan 文本保护
_MAX_KEYWORDS = 64

# 中文停用词集（简化版；后续可换 jieba/hanlp 词表）
_STOPWORDS_ZH = frozenset({
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "这",
    "那", "一", "也", "就", "和", "与", "及", "而", "或", "但",
    "为", "以", "于", "至", "从", "对", "向", "把", "被", "使",
})

# 中文字符正则（仅匹配 CJK 基本区；不包含扩展）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 「整串均为 CJK」判定正则（V2.0 Wave C P2-4：用于 entity 名长度>8 时的纯 CJK 过滤）
_CJK_FULL_RE = re.compile(r"^[\u4e00-\u9fff]+$")

# 非中英文标点 / 空白正则（用于切词边界）
_NON_WORD_RE = re.compile(r"[\s\u3000\uff00-\uffef\u2000-\u206f\u3000-\u303f!" \
                          r"#\$%\&\*\+,\-\.\/:;<=>\?@\[\]\^_\{\|\}~`]+")

# ---------------------------------------------------------------------------
# 关键词提取
# ---------------------------------------------------------------------------


def extract_keywords(plan_text: str, entity_names: list[str] | None = None) -> list[str]:
    """从章节计划文本提取关键词列表。

    口径（V2.0 Wave C 任务一）：
    - 中文按 2-gram（bigram）切词；
    - 英文/数字按空格/标点切词；
    - 停用词过滤；
    - 实体名（character_changes_planned 等）作为整体 token 拼接在最前。

    返回去重后的关键词列表，长度上限 ``_MAX_KEYWORDS``。
    """
    if not plan_text:
        plan_text = ""
    keywords: list[str] = []
    seen: set[str] = set()

    # 实体名优先：作为整体 token，便于 FTS5 整词命中
    if entity_names:
        for name in entity_names:
            if not isinstance(name, str):
                continue
            token = name.strip()
            if len(token) < 2 or token in seen:
                continue
            # V2.0 Wave C P2-4：纯 CJK 无空格长串（长度 > 8）不作为整词 token——
            # bigram 已覆盖所有相邻 2 字组合，作为整词查询反而拉低 bm25 相关性
            # （整词命中概率低），还会消耗 _MAX_KEYWORDS 配额。短串（≤8）仍按
            # 整词注入，便于角色名等精确召回。
            if len(token) > 8 and _CJK_FULL_RE.match(token):
                continue
            seen.add(token)
            keywords.append(token)
            if len(keywords) >= _MAX_KEYWORDS:
                return keywords

    # 中文 2-gram
    cjk_chars = _CJK_RE.findall(plan_text)
    for i in range(len(cjk_chars) - 1):
        bigram = cjk_chars[i] + cjk_chars[i + 1]
        if bigram in _STOPWORDS_ZH or bigram in seen:
            continue
        seen.add(bigram)
        keywords.append(bigram)
        if len(keywords) >= _MAX_KEYWORDS:
            return keywords

    # 英文/数字：unicode61 默认切词；停用词过滤
    for token in _NON_WORD_RE.split(plan_text):
        t = token.strip().lower()
        if not t or len(t) < 2:
            continue
        if t in _STOPWORDS_ZH or t in seen:
            continue
        seen.add(t)
        keywords.append(t)
        if len(keywords) >= _MAX_KEYWORDS:
            break

    return keywords


# ---------------------------------------------------------------------------
# CJK bigram 索引（让 FTS5 unicode61 能按词命中中文）
# ---------------------------------------------------------------------------


def _index_text(text: str) -> str:
    """把原文转成 FTS5 可索引字符串：CJK 按 2-gram 切片 + 空格分隔。

    例子：``"林轩握碎古镜"`` → ``"林轩 握碎 古镜"``。
    非 CJK（英文 / 数字 / 标点）保留原样，由 FTS5 unicode61 默认切词。
    """
    if not text:
        return ""
    # 先把整段按 CJK / 非 CJK 切；CJK 段做 bigram，非 CJK 段保留（unicode61 切）。
    out_parts: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if _CJK_RE.match(ch):
            # 收集连续 CJK
            j = i
            while j < n and _CJK_RE.match(text[j]):
                j += 1
            cjk_segment = text[i:j]
            # bigram 切片
            for k in range(len(cjk_segment) - 1):
                out_parts.append(cjk_segment[k:k + 2])
            i = j
        else:
            # 非 CJK 段：累积到下一个 CJK 或标点
            j = i
            while j < n and not _CJK_RE.match(text[j]):
                j += 1
            seg = text[i:j].strip()
            if seg:
                out_parts.append(seg)
            i = j
    return " ".join(out_parts)


# ---------------------------------------------------------------------------
# 索引维护
# ---------------------------------------------------------------------------


def _chapter_content(conn, chapter_id: str) -> str | None:
    """取章节正文（取该章最新 draft 的 content）；无 draft → None。"""
    row = conn.execute(
        """
        SELECT content FROM drafts
        WHERE chapter_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (chapter_id,),
    ).fetchone()
    if row is None:
        return None
    return row["content"] or ""


def rebuild_index(db_path: str, project_id: str | None = None) -> int:
    """重建 FTS 索引。

    - ``project_id=None`` → 清空 chapter_fts 并按 chapters 表全文重建；
    - ``project_id="<id>"`` → 仅重建该 project 的章节。

    返回处理的 chapter 行数（重索引条数）。
    """
    conn = get_connection(db_path)
    try:
        if project_id is not None:
            rows = conn.execute(
                "SELECT chapter_id FROM chapters WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            count = 0
            for r in rows:
                cid = r["chapter_id"]
                content = _chapter_content(conn, cid) or ""
                _upsert_fts_row(conn, cid, content)
                count += 1
            conn.commit()
            return count
        conn.execute("DELETE FROM chapter_fts")
        rows = conn.execute(
            "SELECT chapter_id FROM chapters"
        ).fetchall()
        count = 0
        for r in rows:
            cid = r["chapter_id"]
            content = _chapter_content(conn, cid) or ""
            _upsert_fts_row(conn, cid, content)
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def _upsert_fts_row(conn, chapter_id: str, content: str) -> None:
    """单行 upsert 到 chapter_fts（同一连接复用）。"""
    conn.execute(
        "DELETE FROM chapter_fts WHERE chapter_id = ?", (chapter_id,),
    )
    conn.execute(
        "INSERT INTO chapter_fts(chapter_id, content) VALUES (?, ?)",
        (chapter_id, _index_text(content)),
    )


def upsert_chapter(db_path: str, chapter_id: str) -> bool:
    """单章 upsert 进 FTS。

    实现：删旧（chapter_id 维度）+ 插新（INSERT OR REPLACE 兼容）。
    失败按 summarize 节点相同语义降级（log warning，不抛错）。
    """
    try:
        conn = get_connection(db_path)
        try:
            content = _chapter_content(conn, chapter_id)
            if content is None:
                conn.execute(
                    "DELETE FROM chapter_fts WHERE chapter_id = ?", (chapter_id,),
                )
                conn.commit()
                _log.warning(
                    "fts_upsert skipped (no draft): chapter_id=%s", chapter_id,
                )
                return False
            _upsert_fts_row(conn, chapter_id, content)
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 —— 降级：失败不抛错
        _log.warning(
            "fts_upsert degraded: chapter_id=%s err=%s", chapter_id, exc,
        )
        return False


# ---------------------------------------------------------------------------
# 召回
# ---------------------------------------------------------------------------


def search(
    db_path: str,
    project_id: str,
    query: str,
    *,
    limit: int = _RECALL_TOP_N,
    current_chapter_id: str | None = None,
    snippet_max_chars: int = _SNIPPET_MAX_CHARS,
) -> list[dict[str, Any]]:
    """按 query 字符串在 chapter_fts 上检索该项目下的相关历史片段。

    返回 ``[{"chapter_id", "chapter_no", "snippet", "rank"}, ...]``，
    按 FTS5 bm25 升序排（值越小越相关）；空 list 表示无命中或无索引。

    - ``current_chapter_id`` 传值时自动排除当前章（避免自召自）。
    - snippet 直接从 ``drafts.content`` 取原文，截断到 ``snippet_max_chars`` 字符。
    """
    if not query or not query.strip():
        return []
    conn = get_connection(db_path)
    try:
        tokens = [t for t in re.split(r"\s+", query.strip()) if t]
        if not tokens:
            return []
        # FTS5 match 表达式：OR 关系；整词用双引号包避免被分词。
        # V2.0 Wave C P2-1：token 内若含双引号，按 FTS5 字符串转义规则先把
        # ``"`` 替换为 ``""``（两个连续双引号表示字符串内的一个双引号字符）；
        # 否则含引号 query 会触发 FTS5 表达式非法降级 → 空 list（用户感受为
        # 「明明 query 合法却召回空」）。
        match_expr = " OR ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens)
        # 排除当前章 + 项目过滤（在 JOIN 条件上）
        params: list[Any] = [match_expr, project_id]
        exclude_clause = ""
        if current_chapter_id is not None:
            exclude_clause = "AND cf.chapter_id != ?"
            params.append(current_chapter_id)
        params.append(int(limit))
        try:
            rows = conn.execute(
                f"""
                SELECT cf.chapter_id, ch.number AS chapter_no, rank,
                       (
                           SELECT d.content FROM drafts d
                           WHERE d.chapter_id = cf.chapter_id
                           ORDER BY d.created_at DESC LIMIT 1
                       ) AS raw_content
                FROM chapter_fts cf
                JOIN chapters ch ON ch.chapter_id = cf.chapter_id
                WHERE chapter_fts MATCH ?
                  AND ch.project_id = ?
                  {exclude_clause}
                ORDER BY rank ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 —— FTS 表达式非法等
            _log.warning(
                "fts_search degraded: project_id=%s query=%r err=%s",
                project_id, query, exc,
            )
            return []
        results: list[dict[str, Any]] = []
        for r in rows:
            raw = r["raw_content"] or ""
            # 找第一个 query token 在原文里的位置（无则取头）
            snip = _extract_snippet(raw, tokens, snippet_max_chars)
            results.append({
                "chapter_id": r["chapter_id"],
                "chapter_no": int(r["chapter_no"]) if r["chapter_no"] is not None else None,
                "snippet": snip,
                "rank": float(r["rank"]) if r["rank"] is not None else 0.0,
            })
        return results
    finally:
        conn.close()


def _extract_snippet(raw: str, tokens: list[str], max_chars: int) -> str:
    """从原文 raw 中找第一个 token 的命中位置，截取 ≤ max_chars 字符。"""
    if not raw:
        return ""
    pos = -1
    for t in tokens:
        if not t:
            continue
        idx = raw.find(t)
        if idx >= 0 and (pos < 0 or idx < pos):
            pos = idx
    if pos < 0:
        # 无命中位置（罕见，因为 query token 通常来自原文）：取头
        snippet = raw[:max_chars]
    else:
        # 命中位置前留 30 字符上下文
        start = max(0, pos - 30)
        snippet = raw[start:start + max_chars]
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars]
    # V2.0 Wave C P2-5：拆行重写三元嵌套条件——行为不变，可读性提升。
    if pos >= 0:
        raw_exceeds = len(raw) > start + max_chars
    else:
        raw_exceeds = len(raw) > max_chars
    if raw_exceeds:
        if not snippet.endswith("…"):
            snippet = snippet + "…"
    return snippet


__all__ = [
    "extract_keywords",
    "rebuild_index",
    "search",
    "upsert_chapter",
    "_SNIPPET_MAX_CHARS",
    "_RECALL_TOP_N",
]
