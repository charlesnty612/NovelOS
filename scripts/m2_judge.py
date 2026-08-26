"""M2 章质量评审探针 —— 用真实 LLM 对已提交章节做"好看"程度的四维量化评分。
本脚本是独立探针，不接入任何 workflow、不修改任何源文件、绝不把 API 密钥写入文件或日志。

设计要点：
- 数据源：``data/m1_run/novelos.db`` 中 ``status='COMMITTED'`` 的章节；正文取该章
  ``drafts`` 表中 ``version`` 最大的记录。
- LLM：openai 兼容协议直调 ``{base_url}/chat/completions``，默认
  ``base_url=https://api.minimaxi.com/v1``、``model=MiniMax-M3``，鉴权用环境变量
  ``MINIMAX_API_KEY``（从不写入文件/日志/JSON）。
- 响应：要求 LLM 输出严格 JSON（pacing/style/logic/dialogue 四维 0-100 整数、
  verdict ≤50 字、top_issues 列表）；剥离 ``<think>`` 与 code fence 后解析；失败
  注入格式纠错提示自动重试 1 次，再失败该章记 error 跳过。
- ``--dry-run``：不调 API，用固定假响应走通全流程，便于在无密钥 / 离线场景验收。
- 断点续跑：已写入 ``judge_results.json`` 的章号默认跳过；``--force`` 强制重评。
- 输出：``<out-dir>/judge_results.json``（逐章明细 + 元数据）与
  ``<out-dir>/judge_summary.md``（汇总表 + 分段趋势 + top_issues 词频）。

V3.1 P1-2 新增：``--persist-api URL`` 可选参数。评审成功（无 ``error`` 字段）的章
会通过 ``POST {URL}/api/chapters/<chapter_id>/quality/judge`` 落库到
``quality_reports.judge_json`` 列；与七子分公式双轨并存。失败/缺四维分/网络错误仅
log warning，不阻断主流程（评审结果仍写到 ``judge_results.json``）。**默认不传
``--persist-api`` 时与原 V3.0 行为完全一致**。

用法：
    # 默认 dry-run：评审 50 章真实语料，验证脚本本身
    python scripts/m2_judge.py --dry-run

    # 真实评审：需要设置 MINIMAX_API_KEY
    export MINIMAX_API_KEY=sk-...
    python scripts/m2_judge.py --chapters 1-5,8,11-20 --out-dir docs/evaluation/m2-judge

    # 限定章号 + 自定义端点
    python scripts/m2_judge.py --chapters 1-3 --base-url https://api.minimaxi.com/v1 \\
        --model MiniMax-M3 --out-dir /tmp/m2 --dry-run

    # 强制重跑（覆盖断点）
    python scripts/m2_judge.py --chapters 1-3 --out-dir /tmp/m2 --force

    # 评审后落库（双轨）：先启动后端 ``uvicorn packages.core.api.main:app --port 18091``，
    # 再 ``python scripts/m2_judge.py --dry-run --persist-api http://127.0.0.1:18091``
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# 路径与导入：复用项目内的 think/fence 剥离函数（与 packages.core 一致口径）
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.core.agent_runtime.structured_output import (  # noqa: E402
    extract_json,
    strip_code_fence,
    strip_think_blocks,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_TIMEOUT_S = 600.0
DEFAULT_PERSIST_TIMEOUT_S = 10.0  # POST /quality/judge 落库请求超时
DEFAULT_MAX_CONTENT_CHARS = 60_000  # 单章正文超过则截断到该上限，避免 prompt 爆炸
DEFAULT_DB = "data/m1_run/novelos.db"
DEFAULT_OUT_DIR = "docs/evaluation/m2-judge"

SYSTEM_PROMPT = (
    "你是一位资深的网文编辑评审。请基于标题与正文，对这一章做严格、客观、可量化的"
    "质量评审。\n"
    "要求：\n"
    "1) 仅输出严格 JSON，不要解释、不要 Markdown 围栏、不要任何前后缀文字；\n"
    "2) 评分标准（整数 0-100）：\n"
    "   - pacing：爽感密度与节奏推进（冲突/反转/揭秘每章至少一个爽点为高分）；\n"
    "   - style：文笔与画面感（句式多样、用词准确、意象与白描相宜）；\n"
    "   - logic：情节逻辑与因果链（前后呼应、不自相矛盾、世界规则不冲突）；\n"
    "   - dialogue：对话自然度与信息量（推进情节 / 展现人物，不水对话）；\n"
    "3) verdict：一句话总评（≤50 字），直指本章最强或最弱处；\n"
    "4) top_issues：列出本章最显著的 3 个问题（短句数组，每条 ≤30 字）。\n"
    '严格输出形如：{"pacing": 0, "style": 0, "logic": 0, "dialogue": 0,'
    ' "verdict": "一句话", "top_issues": ["问题1", "问题2", "问题3"]}'
)

FORMAT_RETRY_HINT = (
    "你上一次输出未能解析为合法 JSON。请只输出一个合法 JSON 对象，"
    "形如 {\"pacing\": int, \"style\": int, \"logic\": int, \"dialogue\": int,"
    " \"verdict\": \"≤50字\", \"top_issues\": [\"≤30字\", \"≤30字\", \"≤30字\"]}。"
    "不要 Markdown 围栏、不要任何前后缀。"
)

REQUIRED_KEYS = ("pacing", "style", "logic", "dialogue", "verdict", "top_issues")
SCORE_KEYS = ("pacing", "style", "logic", "dialogue")
RANGE_KEYS = {"pacing", "style", "logic", "dialogue"}

# dry-run 假响应库（按章号取模循环），覆盖典型评分分布
DRY_RUN_RESPONSES: tuple[dict[str, Any], ...] = (
    {
        "pacing": 72,
        "style": 78,
        "logic": 70,
        "dialogue": 65,
        "verdict": "节奏平稳，开场铺设完整，对话偏少。",
        "top_issues": ["冲突未明确给出", "对话占比偏低", "悬念铺设可加强"],
    },
    {
        "pacing": 80,
        "style": 82,
        "logic": 76,
        "dialogue": 74,
        "verdict": "本章节奏推进明显，反转到位，文笔老练。",
        "top_issues": ["配角动机交代仓促", "信息密度可再压缩", "环境描写略冗长"],
    },
    {
        "pacing": 58,
        "style": 70,
        "logic": 65,
        "dialogue": 60,
        "verdict": "情节推进缓慢，爽点缺失，需补充冲突。",
        "top_issues": ["爽点缺失", "节奏拖沓", "主线偏离"],
    },
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def parse_chapter_spec(spec: str | None) -> set[int] | None:
    """解析 --chapters 形如 ``1-5,8,11-20``；空/None 表示不限制（全部）。"""
    if spec is None or not spec.strip():
        return None
    out: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            a, _, b = token.partition("-")
            a_i, b_i = int(a), int(b)
            lo, hi = (a_i, b_i) if a_i <= b_i else (b_i, a_i)
            out.update(range(lo, hi + 1))
        else:
            out.add(int(token))
    return out


def load_committed_chapters(db_path: Path) -> list[dict[str, Any]]:
    """按章号升序返回所有 ``status='COMMITTED'`` 章节的 number/title/chapter_id。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT number, title, chapter_id FROM chapters "
            "WHERE status='COMMITTED' ORDER BY number"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def load_latest_draft(db_path: Path, chapter_id: str) -> str | None:
    """取某章 ``version`` 最大的 draft.content；不存在返回 None。"""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT content FROM drafts WHERE chapter_id=? ORDER BY version DESC LIMIT 1",
            (chapter_id,),
        ).fetchone()
        return None if row is None else row[0]
    finally:
        conn.close()


def truncate_content(text: str, max_chars: int) -> str:
    """按字符数截断；超长则附加省略标记，避免 prompt 失控。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n（……本章正文超过设定上限，已截断；评审时按可见范围打分。）"


def build_user_message(title: str, content: str) -> str:
    """组装 user 消息：标题 + 正文（含字数提示）。"""
    return (
        f"章节标题：{title or '（无标题）'}\n"
        f"正文字数：{len(content)} 字符\n\n"
        f"正文：\n{content}"
    )


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """校验四维分类型与范围、verdict 长度、top_issues 形态；不合法抛 ValueError。
    合法返回归一化后的 dict（分数截到 0-100、verdict 截到 50 字、issues 限 3 条）。"""
    missing = [k for k in REQUIRED_KEYS if k not in payload]
    if missing:
        raise ValueError(f"missing required keys: {missing}")
    cleaned: dict[str, Any] = {}
    for k in SCORE_KEYS:
        v = payload[k]
        if isinstance(v, bool) or not isinstance(v, int):
            # 允许被 JSON 当成 number 的浮点（int()）
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            else:
                raise ValueError(f"{k!r} must be int, got {type(v).__name__}={v!r}")
        cleaned[k] = max(0, min(100, int(v)))
    verdict = payload["verdict"]
    if not isinstance(verdict, str):
        raise ValueError(f"'verdict' must be str, got {type(verdict).__name__}")
    cleaned["verdict"] = verdict.strip()[:50]
    issues = payload["top_issues"]
    if not isinstance(issues, list):
        raise ValueError(f"'top_issues' must be list, got {type(issues).__name__}")
    norm_issues: list[str] = []
    for it in issues[:3]:
        if isinstance(it, str):
            norm_issues.append(it.strip()[:30])
        else:
            norm_issues.append(str(it)[:30])
    while len(norm_issues) < 3:
        norm_issues.append("")
    cleaned["top_issues"] = norm_issues
    return cleaned


def call_llm(
    base_url: str,
    model: str,
    api_key: str,
    title: str,
    content: str,
    timeout: float,
) -> tuple[dict[str, Any], dict[str, int], float]:
    """调用一次 LLM；返回 (校验后的 payload dict, usage dict, elapsed_ms)。

    失败（网络/解析/校验）抛 RuntimeError；调用方决定是否重试。"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(title, content)},
        ],
        "temperature": 0.2,
    }
    t0 = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code >= 400:
        snippet = (resp.text or "")[:200]
        raise RuntimeError(f"HTTP {resp.status_code}: {snippet}")
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"invalid JSON response: {exc}") from exc
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"malformed response: {exc}") from exc
    usage_raw = data.get("usage") or {}
    try:
        prompt_tokens = int(usage_raw.get("prompt_tokens") or 0)
        completion_tokens = int(usage_raw.get("completion_tokens") or 0)
        total_tokens = int(
            usage_raw.get("total_tokens") or (prompt_tokens + completion_tokens)
        )
    except (TypeError, ValueError):
        prompt_tokens = completion_tokens = total_tokens = 0
    usage = {
        "prompt": prompt_tokens,
        "completion": completion_tokens,
        "total": total_tokens,
    }
    cleaned = strip_think_blocks(strip_code_fence(text))
    payload = extract_json(cleaned)  # 不合法抛 AgentOutputError
    payload = validate_payload(payload)  # 形态合法化抛 ValueError
    return payload, usage, elapsed_ms


def dry_run_response(idx: int) -> tuple[dict[str, Any], dict[str, int], float]:
    """--dry-run 用：返回固定假响应。"""
    payload = dict(DRY_RUN_RESPONSES[idx % len(DRY_RUN_RESPONSES)])
    # 让 verdict 与章号挂钩，便于人工一眼看出是 dry-run 假数据
    payload = {**payload, "verdict": f"[dry-run#{idx + 1}] " + payload["verdict"]}
    usage = {"prompt": 0, "completion": 0, "total": 0}
    return payload, usage, 0.0


def judge_one_chapter(
    *,
    chapter: dict[str, Any],
    content: str,
    base_url: str,
    model: str,
    api_key: str | None,
    timeout: float,
    dry_run: bool,
) -> dict[str, Any]:
    """评审单章：含 1 次格式重试；返回结果 dict（含 error 字段或归一化 payload）。"""
    title = chapter["title"] or ""
    number = chapter["number"]
    last_error: str | None = None
    attempts: list[dict[str, Any]] = []
    for attempt_idx in range(2):
        try:
            if dry_run:
                payload, usage, elapsed = dry_run_response(number)
            else:
                payload, usage, elapsed = call_llm(
                    base_url=base_url,
                    model=model,
                    api_key=api_key or "",
                    title=title,
                    content=content,
                    timeout=timeout,
                )
            result = {
                "number": number,
                "title": title,
                "chapter_id": chapter["chapter_id"],
                "pacing": payload["pacing"],
                "style": payload["style"],
                "logic": payload["logic"],
                "dialogue": payload["dialogue"],
                "verdict": payload["verdict"],
                "top_issues": payload["top_issues"],
                "score_avg": round(
                    (
                        payload["pacing"]
                        + payload["style"]
                        + payload["logic"]
                        + payload["dialogue"]
                    )
                    / 4.0,
                    2,
                ),
                "usage": usage,
                "elapsed_ms": elapsed,
                "dry_run": dry_run,
                "attempts": attempt_idx + 1,
                "error": None,
            }
            return result
        except Exception as exc:  # noqa: BLE001
            # 记录原始文本以便调试（dry-run 时为空）
            last_error = f"{type(exc).__name__}: {exc}"
            attempts.append({"attempt": attempt_idx + 1, "error": last_error})
            # 第 1 次失败 → 注入格式纠错提示（dry-run 不需要）
            if attempt_idx == 0 and not dry_run:
                # 不在 call_llm 里加重试（保持原函数纯净），改用单独路径：再次调用时
                # 在 messages 末尾追加纠错提示
                try:
                    payload, usage, elapsed = _call_llm_with_retry_hint(
                        base_url=base_url,
                        model=model,
                        api_key=api_key or "",
                        title=title,
                        content=content,
                        timeout=timeout,
                    )
                    result = {
                        "number": number,
                        "title": title,
                        "chapter_id": chapter["chapter_id"],
                        "pacing": payload["pacing"],
                        "style": payload["style"],
                        "logic": payload["logic"],
                        "dialogue": payload["dialogue"],
                        "verdict": payload["verdict"],
                        "top_issues": payload["top_issues"],
                        "score_avg": round(
                            (
                                payload["pacing"]
                                + payload["style"]
                                + payload["logic"]
                                + payload["dialogue"]
                            )
                            / 4.0,
                            2,
                        ),
                        "usage": usage,
                        "elapsed_ms": elapsed,
                        "dry_run": dry_run,
                        "attempts": 2,
                        "error": None,
                    }
                    return result
                except Exception as exc2:  # noqa: BLE001
                    last_error = f"{type(exc2).__name__}: {exc2}"
                    attempts.append({"attempt": 2, "error": last_error})
                    break
            else:
                break
    # 走到这里说明两次都失败
    return {
        "number": number,
        "title": title,
        "chapter_id": chapter["chapter_id"],
        "pacing": None,
        "style": None,
        "logic": None,
        "dialogue": None,
        "verdict": None,
        "top_issues": None,
        "score_avg": None,
        "usage": {"prompt": 0, "completion": 0, "total": 0},
        "elapsed_ms": 0,
        "dry_run": dry_run,
        "attempts": len(attempts),
        "error": last_error or "unknown",
        "attempts_log": attempts,
    }


def _call_llm_with_retry_hint(
    *,
    base_url: str,
    model: str,
    api_key: str,
    title: str,
    content: str,
    timeout: float,
) -> tuple[dict[str, Any], dict[str, int], float]:
    """第二次调用：在 messages 末尾追加 user 提示，让 LLM 重新生成严格 JSON。"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(title, content)},
            {"role": "user", "content": FORMAT_RETRY_HINT},
        ],
        "temperature": 0.0,
    }
    t0 = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code >= 400:
        snippet = (resp.text or "")[:200]
        raise RuntimeError(f"HTTP {resp.status_code}: {snippet}")
    data = resp.json()
    text = data["choices"][0]["message"]["content"] or ""
    usage_raw = data.get("usage") or {}
    prompt_tokens = int(usage_raw.get("prompt_tokens") or 0)
    completion_tokens = int(usage_raw.get("completion_tokens") or 0)
    total_tokens = int(usage_raw.get("total_tokens") or (prompt_tokens + completion_tokens))
    usage = {
        "prompt": prompt_tokens,
        "completion": completion_tokens,
        "total": total_tokens,
    }
    cleaned = strip_think_blocks(strip_code_fence(text))
    payload = extract_json(cleaned)
    payload = validate_payload(payload)
    return payload, usage, elapsed_ms


# ---------------------------------------------------------------------------
# 结果读写（断点续跑 + 原子追加）
# ---------------------------------------------------------------------------


def load_existing_results(out_dir: Path) -> list[dict[str, Any]]:
    """读取已有 judge_results.json；不存在返回空 list。"""
    p = out_dir / "judge_results.json"
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        results = data.get("results", [])
        return results if isinstance(results, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_results(out_dir: Path, results: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    """原子写：先写 .tmp 再 rename，避免半章中断导致 JSON 损坏。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / "judge_results.json"
    tmp = out_dir / "judge_results.json.tmp"
    payload = {"meta": meta, "results": results}
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(final)


def persist_judge_to_api(
    api_base_url: str,
    chapter_id: str,
    judge_payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_PERSIST_TIMEOUT_S,
) -> tuple[bool, str]:
    """V3.1 P1-2：把单章 LLM judge 结果 POST 到 ``/api/chapters/{cid}/quality/judge``。

    入参 ``judge_payload`` 已是 ``judge_one_chapter`` 返回的 dict（含 pacing/style/
    logic/dialogue/verdict/top_issues/score_avg/usage/dry_run/error 等）；直接
    转发；后端 service 会做四维校验。

    返回 ``(ok, detail)``：``ok=True`` 表示落库成功；``ok=False`` 时 ``detail``
    是错误原因（HTTP 状态码 + 摘要）。任何错误均不抛（让脚本继续评审下一章），
    由调用方决定是否记 warning。

    设计要点：
    - 默认超时 10s（落库是轻量请求），可被 timeout 参数覆盖（仅测试需要）；
    - 不写 API key / 评测元数据到日志（payload 已经无敏感信息；
      verdict / top_issues 含中文，正常打印属预期行为）；
    - 4xx/5xx/网络错误一律返回 ``(False, detail)``，不抛异常。
    """
    url = f"{api_base_url.rstrip('/')}/api/chapters/{chapter_id}/quality/judge"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=judge_payload)
    except Exception as exc:  # noqa: BLE001
        return False, f"network: {type(exc).__name__}: {exc}"
    if 200 <= resp.status_code < 300:
        return True, ""
    snippet = (resp.text or "")[:200]
    return False, f"HTTP {resp.status_code}: {snippet}"


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]+")


def tokenize_zh(text: str) -> list[str]:
    """中文友好分词：连续中英文/数字视为一个 token；过滤单字噪声。"""
    return [t for t in _TOKEN_RE.findall(text) if len(t) >= 2]


def build_summary_markdown(
    results: list[dict[str, Any]],
    meta: dict[str, Any],
) -> str:
    """生成汇总 Markdown：逐章表 + 分段趋势 + top_issues 词频 + 错误清单。"""
    parts: list[str] = []
    parts.append("# M2 章质量评审探针 —— 汇总报告")
    parts.append("")
    parts.append(f"- 数据库：`{meta.get('db_path')}`")
    parts.append(f"- LLM：`{meta.get('model')}` @ `{meta.get('base_url')}`")
    parts.append(f"- dry-run：`{meta.get('dry_run')}`")
    parts.append(f"- 共评审章节：{len(results)}")
    parts.append(f"- 总耗时：{meta.get('total_elapsed_ms', 0) / 1000:.1f}s")
    parts.append("")
    parts.append("---")
    parts.append("")

    # 1) 逐章表
    parts.append("## 1. 逐章评分")
    parts.append("")
    parts.append("| 章号 | 标题 | pacing | style | logic | dialogue | 均分 | verdict |")
    parts.append("| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for r in results:
        if r.get("error"):
            parts.append(
                f"| {r['number']} | {r.get('title') or ''} | - | - | - | - | - | "
                f"ERROR: {(r['error'] or '')[:40]} |"
            )
        else:
            parts.append(
                f"| {r['number']} | {r.get('title') or ''} | "
                f"{r['pacing']} | {r['style']} | {r['logic']} | {r['dialogue']} | "
                f"{r['score_avg']} | {r['verdict']} |"
            )
    parts.append("")

    # 2) 分段趋势（每 10 章四维均值）
    parts.append("## 2. 分段均值趋势（每 10 章）")
    parts.append("")
    valid = [r for r in results if not r.get("error")]
    if valid:
        buckets: dict[int, list[dict[str, Any]]] = {}
        for r in valid:
            bucket = (r["number"] - 1) // 10
            buckets.setdefault(bucket, []).append(r)
        parts.append("| 区间 | 章数 | pacing | style | logic | dialogue | 均分 |")
        parts.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for bucket in sorted(buckets):
            rows = buckets[bucket]
            lo = min(r["number"] for r in rows)
            hi = max(r["number"] for r in rows)

            def avg_of(key: str, rs: list[dict[str, Any]] = rows) -> float:
                return round(sum(r[key] for r in rs) / len(rs), 2)

            parts.append(
                f"| {lo}-{hi} | {len(rows)} | "
                f"{avg_of('pacing')} | {avg_of('style')} | {avg_of('logic')} | "
                f"{avg_of('dialogue')} | {avg_of('score_avg')} |"
            )
        parts.append("")

    # 3) top_issues 词频 top10
    parts.append("## 3. top_issues 词频 Top 10")
    parts.append("")
    counter: Counter[str] = Counter()
    for r in valid:
        for issue in r.get("top_issues") or []:
            for tok in tokenize_zh(issue):
                counter[tok] += 1
    if counter:
        parts.append("| 排名 | 词 | 频次 |")
        parts.append("| ---: | --- | ---: |")
        for rank, (tok, n) in enumerate(counter.most_common(10), 1):
            parts.append(f"| {rank} | {tok} | {n} |")
    else:
        parts.append("（无可统计的 top_issues）")
    parts.append("")

    # 4) 错误与跳过清单
    errored = [r for r in results if r.get("error")]
    if errored:
        parts.append("## 4. 错误章节")
        parts.append("")
        for r in errored:
            parts.append(
                f"- 第 {r['number']} 章 `{r.get('title') or ''}`：{r.get('error') or ''}"
            )
        parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("明细：`judge_results.json`（含每章 usage 与耗时）")
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M2 章质量评审探针 —— 真实 LLM 四维量化评分",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"SQLite 库路径（默认 {DEFAULT_DB}）",
    )
    parser.add_argument(
        "--chapters",
        default=None,
        help="章号过滤，形如 1-5,8,11-20；空=全部 COMMITTED 章节",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"产物目录（默认 {DEFAULT_OUT_DIR}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不调 API，用固定假响应跑通全流程",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重跑覆盖断点（默认跳过已存在章号）",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"LLM base_url（默认 {DEFAULT_BASE_URL}）",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"模型名（默认 {DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"单章 LLM 超时（秒，默认 {DEFAULT_TIMEOUT_S}）",
    )
    parser.add_argument(
        "--max-content-chars",
        type=int,
        default=DEFAULT_MAX_CONTENT_CHARS,
        help=f"单章正文超过该字符数则截断（默认 {DEFAULT_MAX_CONTENT_CHARS}）",
    )
    parser.add_argument(
        "--persist-api",
        default=None,
        help=(
            "V3.1 P1-2 可选：评审后逐章 POST 到 "
            "{base_url}/api/chapters/<cid>/quality/judge 落库到 "
            "quality_reports.judge_json；与七子分公式双轨并存。默认不传=原行为。"
        ),
    )
    parser.add_argument(
        "--persist-timeout",
        type=float,
        default=DEFAULT_PERSIST_TIMEOUT_S,
        help=f"落库请求超时（秒，默认 {DEFAULT_PERSIST_TIMEOUT_S}）",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    out_dir = Path(args.out_dir).resolve()

    if not db_path.exists():
        print(f"[m2-judge] FAIL：数据库不存在 {db_path}", flush=True)
        return 2

    api_key: str | None = None
    if not args.dry_run:
        api_key = os.environ.get("MINIMAX_API_KEY")
        if not api_key:
            print(
                "[m2-judge] FAIL：未设置 MINIMAX_API_KEY 且未传 --dry-run",
                flush=True,
            )
            return 2

    all_chapters = load_committed_chapters(db_path)
    spec = parse_chapter_spec(args.chapters)
    if spec is not None:
        chapters = [c for c in all_chapters if c["number"] in spec]
        if not chapters:
            print(
                f"[m2-judge] FAIL：--chapters 过滤后无章节（spec={args.chapters}）",
                flush=True,
            )
            return 2
    else:
        chapters = all_chapters

    existing = [] if args.force else load_existing_results(out_dir)
    existing_numbers = {r["number"] for r in existing if "number" in r}
    pending = [c for c in chapters if c["number"] not in existing_numbers]
    skipped = [c for c in chapters if c["number"] in existing_numbers]

    print(
        f"[m2-judge] db={db_path} 共 {len(all_chapters)} 章 COMMITTED；"
        f"待评审 {len(pending)} 章；跳过 {len(skipped)} 章（已落盘）",
        flush=True,
    )
    if not pending:
        print("[m2-judge] 无需评审（全部已完成）", flush=True)

    results: list[dict[str, Any]] = list(existing)
    total_elapsed_ms = 0
    total_tokens = 0
    started_at = time.time()
    for idx, chapter in enumerate(pending):
        content = load_latest_draft(db_path, chapter["chapter_id"])
        if not content:
            results.append(
                {
                    "number": chapter["number"],
                    "title": chapter["title"],
                    "chapter_id": chapter["chapter_id"],
                    "pacing": None,
                    "style": None,
                    "logic": None,
                    "dialogue": None,
                    "verdict": None,
                    "top_issues": None,
                    "score_avg": None,
                    "usage": {"prompt": 0, "completion": 0, "total": 0},
                    "elapsed_ms": 0,
                    "dry_run": args.dry_run,
                    "attempts": 0,
                    "error": "draft content empty or missing",
                }
            )
            print(
                f"[m2-judge] 第 {chapter['number']} 章 无 draft，跳过",
                flush=True,
            )
            continue
        truncated = truncate_content(content, args.max_content_chars)
        result = judge_one_chapter(
            chapter=chapter,
            content=truncated,
            base_url=args.base_url,
            model=args.model,
            api_key=api_key,
            timeout=args.timeout,
            dry_run=args.dry_run,
        )
        results.append(result)
        total_elapsed_ms += int(result.get("elapsed_ms") or 0)
        total_tokens += int((result.get("usage") or {}).get("total") or 0)
        # 每章落盘一次（断点续跑更稳）
        meta = {
            "db_path": str(db_path),
            "base_url": args.base_url,
            "model": args.model,
            "dry_run": args.dry_run,
            "total_elapsed_ms": total_elapsed_ms,
            "total_tokens": total_tokens,
            "started_at": started_at,
            "updated_at": time.time(),
            "completed": sum(1 for r in results if not r.get("error")),
            "errored": sum(1 for r in results if r.get("error")),
        }
        save_results(out_dir, results, meta)
        # V3.1 P1-2：--persist-api 可选落库；与 save_results 解耦，失败仅 warning
        # 不阻断主流程（评审结果已经在 judge_results.json 内，persistence 是双轨可选项）。
        if args.persist_api and not result.get("error"):
            ok, detail = persist_judge_to_api(
                args.persist_api,
                result["chapter_id"],
                result,
                timeout=args.persist_timeout,
            )
            mark = "PERSISTED" if ok else "PERSIST-WARN"
            print(
                f"[m2-judge]   └─ {mark} 第 {chapter['number']} 章 "
                f"{'ok' if ok else detail}",
                flush=True,
            )
        status = "OK" if not result.get("error") else "ERR"
        print(
            f"[m2-judge] [{idx + 1}/{len(pending)}] 第 {chapter['number']} 章 "
            f"{status} ({int(result.get('elapsed_ms') or 0)}ms, "
            f"tokens={(result.get('usage') or {}).get('total') or 0})",
            flush=True,
        )

    # 汇总报告
    meta = {
        "db_path": str(db_path),
        "base_url": args.base_url,
        "model": args.model,
        "dry_run": args.dry_run,
        "total_elapsed_ms": total_elapsed_ms,
        "total_tokens": total_tokens,
        "started_at": started_at,
        "updated_at": time.time(),
        "completed": sum(1 for r in results if not r.get("error")),
        "errored": sum(1 for r in results if r.get("error")),
    }
    save_results(out_dir, results, meta)
    summary_md = build_summary_markdown(results, meta)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "judge_summary.md").write_text(summary_md, encoding="utf-8")

    print(
        f"[m2-judge] DONE：完成 {meta['completed']} 章 / 错误 {meta['errored']} 章；"
        f"产物 {out_dir / 'judge_results.json'} 与 {out_dir / 'judge_summary.md'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
