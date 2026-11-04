"""B-2 LLM 调用面与模型路由实证探针（2026-11-04 B 档逐条实证批）。

覆盖（审计报告 §四 B-2）：
- B2-1 空流重试 ``exc.__class__(msg)`` 重建异常 ⇒ TypeError（预期**推翻**：
  全仓该 marker 唯一产地是 ProviderError，isinstance 分支已特判）。
- B2-2 Anthropic/Ollama provider 构造不传 timeout ⇒ 档案级 ``timeout_s`` 静默忽略。
- B2-3 ``validate_contract`` 对未登记 ``expected``（summarizer）静默跳过契约校验。
- B2-6 ``_upsert_prompt``「内容未变」分支直接 return 不 commit ⇒
  ``_upsert_agent`` 对 role/config_json 的刷新被回滚。

只写仓外临时目录；零真实 LLM 调用。
"""

from __future__ import annotations

import json

from probe_b_common import (  # noqa: I001  # pylint: disable=wrong-import-order
    Recorder,
    force_utf8_stdout,
    make_temp_db,
    verify_worktree_import,
)

force_utf8_stdout()
verify_worktree_import()

from pathlib import Path  # noqa: E402

import packages  # noqa: E402
from packages.core.agent_runtime.structured_output import validate_contract  # noqa: E402
from packages.core.db import get_connection  # noqa: E402
from packages.core.model_router.router import ModelRouter  # noqa: E402


def main() -> int:
    rec = Recorder("B-2 LLM 调用面与模型路由")
    db_path, tmp = make_temp_db("b2")

    # ---------------------------------------------------------------- B2-1
    # marker 唯一产地：全仓源码扫 "empty stream: no content chunks received" 的
    # raise 点，确认全部包在 ProviderError 里。
    pkg_root = Path(packages.__path__[0])  # type: ignore[attr-defined]
    raise_sites: list[str] = []
    for py in pkg_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if "empty stream: no content chunks received" in line and "raise" in line:
                raise_sites.append(f"{py.name}:{i}")
    rec.refute(
        "B2-1 空流重试 exc.__class__(msg) 重建异常 ⇒ 生产路径 TypeError",
        f"marker 的 raise 点实测 {raise_sites}（全部是 providers.py 的 "
        f"ProviderError；runner.py:478-491 对 ProviderError 有 isinstance 特判，"
        f"else 分支对携带 marker 的异常不可达 ⇒ TypeError 不可达）。"
        f"残余边界：第三方自定义 provider 若抛非 ProviderError 且消息含该串才会进 else",
    ) if all("providers.py" in s for s in raise_sites) and raise_sites else rec.confirm(
        "B2-1 空流重试异常重建 ⇒ TypeError", f"raise 点 {raise_sites}",
    )

    # ---------------------------------------------------------------- B2-2
    timeout_s = 123
    row_a = {
        "config_id": "mprof_b2a", "profile_id": "mprof_b2a",
        "provider": "anthropic", "model": "claude-x",
        "params_json": json.dumps({
            "model": "claude-x", "api_key": "sk-probe", "timeout_s": timeout_s,
        }),
        "connection_id": None,
    }
    row_o = {
        "config_id": "mprof_b2o", "profile_id": "mprof_b2o",
        "provider": "ollama", "model": "qwen-x",
        "params_json": json.dumps({
            "model": "qwen-x", "base_url": "http://127.0.0.1:11434",
            "timeout_s": timeout_s,
        }),
        "connection_id": None,
    }
    row_openai = {
        "config_id": "mprof_b2c", "profile_id": "mprof_b2c",
        "provider": "openai_compatible", "model": "glm-x",
        "params_json": json.dumps({
            "model": "glm-x", "api_key": "sk-probe",
            "base_url": "http://127.0.0.1:9000", "timeout_s": timeout_s,
        }),
        "connection_id": None,
    }
    router = ModelRouter(db_path)
    pa = router.get_provider(row_a)
    po = router.get_provider(row_o)
    pc = router.get_provider(row_openai)
    ta, to, tc = pa.timeout, po.timeout, pc.timeout
    rec.confirm(
        "B2-2 档案级 timeout_s 对 anthropic/ollama 静默忽略（固定 60.0s）",
        f"timeout_s=123 ⇒ anthropic 实测 provider.timeout={ta}、"
        f"ollama 实测 provider.timeout={to}（均 = 构造器默认 60.0，"
        f"router.py:417-427 构造时不传 timeout）；对照组 openai_compatible "
        f"timeout={tc}（生效）。⇒ 用户把超时调到 123s 对这两家协议不生效，"
        f"零告警；长文生成会在 60s 被掐断",
    ) if (ta == 60.0 and to == 60.0 and tc == 123) else rec.refute(
        "B2-2 档案级 timeout_s 对 anthropic/ollama 静默忽略",
        f"anthropic={ta} ollama={to} openai_compatible={tc}",
    )

    # ---------------------------------------------------------------- B2-3
    garbage = {"total_summary": "≠契约形状", "extra": [1, 2, 3]}
    raised = ""
    try:
        validate_contract("summarizer", garbage)
    except Exception as exc:  # noqa: BLE001
        raised = f"{type(exc).__name__}: {exc}"
    rec.confirm(
        "B2-3 validate_contract 对 summarizer（未登记 expected）静默跳过契约校验",
        f"validate_contract('summarizer', {garbage}) ⇒ "
        f"{'抛 ' + raised if raised else '不抛（只保证合法 JSON，形状零校验）'}；"
        f"消费点实测 observer.py:479 run_agent(..., expected='summarizer')，"
        f"而 structured_output.py:835 _VALIDATORS 无 summarizer ⇒ "
        f"summarizer 产出只过 JSON 合法性（contract 缺口）",
    ) if not raised else rec.refute(
        "B2-3 validate_contract 对 summarizer 静默跳过", raised,
    )

    # ---------------------------------------------------------------- B2-6
    from packages.core.agent_runtime.prompts import PromptRegistry

    reg = PromptRegistry(db_path)
    # 第一次 upsert：建 agent + prompt（INSERT 分支带 commit ⇒ role 正确落库）。
    reg._upsert_prompt("summarizer", 1, "PROBE-CONTENT-V1")  # noqa: SLF001
    # 人为把 role 改脏（模拟 V3.9.4 要修的存量失真）。
    conn = get_connection(db_path)
    try:
        conn.execute("UPDATE agents SET role = 'bogus-stale' WHERE name = 'summarizer'")
        conn.commit()
    finally:
        conn.close()
    # 第二次 upsert：内容未变 ⇒ 走「完全跳过」分支。
    reg._upsert_prompt("summarizer", 1, "PROBE-CONTENT-V1")  # noqa: SLF001
    conn = get_connection(db_path)
    try:
        role_after_skip = conn.execute(
            "SELECT role FROM agents WHERE name = 'summarizer'"
        ).fetchone()["role"]
    finally:
        conn.close()
    # 对照：内容变化 ⇒ UPDATE 分支带 commit ⇒ role 刷新落库。
    reg._upsert_prompt("summarizer", 1, "PROBE-CONTENT-V2")  # noqa: SLF001
    conn = get_connection(db_path)
    try:
        role_after_change = conn.execute(
            "SELECT role FROM agents WHERE name = 'summarizer'"
        ).fetchone()["role"]
    finally:
        conn.close()
    rec.confirm(
        "B2-6 prompts._upsert_agent 的 role/config_json 刷新在「内容未变」分支被回滚",
        f"人为把 role 改成 'bogus-stale' 后：内容未变分支 ⇒ DB role="
        f"{role_after_skip!r}（刷新被回滚——close 未 commit ⇒ 隐式回滚）；"
        f"内容变化分支 ⇒ DB role={role_after_change!r}（正常落库）。"
        f"⇒ V3.9.4 的『启动 sync 自动对齐 role』只在 prompt 内容恰好变化时生效",
    ) if role_after_skip == "bogus-stale" and role_after_change == "light" else rec.refute(
        "B2-6 role 刷新被回滚",
        f"skip 分支后 role={role_after_skip!r}，change 分支后 role={role_after_change!r}",
    )

    print(f"\n（临时库：{tmp}）")
    return rec.summary()


if __name__ == "__main__":
    raise SystemExit(main())
