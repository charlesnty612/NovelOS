"""只读探针：核实「撤掉档案列表的脱敏出口」到底让**哪几条**用例变红。

背景：run_spec 报 L3M15 红在 ``test_model_profiles_list_returns_the_measured_ten_keys``，
但那条用例只断言「顶层键集」+「disabled 档的 params_json == {}」——按读码推理，
它变红的原因应该是**后者**（脱敏把 None 规整成 {}），而不是规格注释声称的
「明文 api_key 出参」。而规格真正想守的
``test_list_rows_never_leak_the_plaintext_api_key`` 在文件里更靠后 ⇒ runner 只报了
第一条 FAILED 行。⇒ **判别力归属需要实测，不能靠推理。**

实测结论（2026-10-21）：**两条都红**，但红因**不是**明文出参，而是**端点 500**
（DB 里 ``params_json`` 是 JSON 字符串 ⇒ 进不了 ``dict[str, Any]`` 校验）。

本脚本：备份 → 突变 → 跑两条用例并逐条报结果 → finally 还原 + 校验 SHA。

⚠️ **本脚本会临时改仓库源码** ⇒ 与 pytest / 突变执行器**禁并发**
（AGENTS 坑区「预检与执行器并发」同族：并发读被改坏的源码会报出假结论）。
启动时若发现 ``*.mutbak`` 残留即拒绝运行。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

# ⚠️ 本机控制台默认 cp936：中文与 emoji 都会炸（AGENTS 坑区「Windows 默认 cp936
# 吞中文」+「控制台乱码一律先怀疑管道」）。输出流改 utf-8。
# ⚠️ 判 hasattr：pytest 捕获期间 sys.stdout 被换成没有 reconfigure 的对象 ⇒
# 无条件调用会让 **import 就炸**。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "packages/core/api/routers/model_profiles.py"
BAK = TARGET.with_suffix(".py.probebak")

OLD = (
    "    out = []\n"
    "    for r in rows:\n"
    '        cid = r.get("connection_id")\n'
    "        out.append(\n"
    "            _mask_response(r, connection_row=conns.get(cid)) if cid else _mask_response(r)\n"
    "        )\n"
    "    return out\n"
)
NEW = (
    "    out = []\n"
    "    for r in rows:\n"
    '        cid = r.get("connection_id")\n'
    "        out.append(r)\n"
    "    return out\n"
)

TESTS = [
    "tests/api/test_config_list_response_contract.py::test_list_rows_never_leak_the_plaintext_api_key",
    "tests/api/test_config_list_response_contract.py::test_model_profiles_list_returns_the_measured_ten_keys",
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _assert_no_mutation_runner() -> None:
    """突变执行器在飞时**拒绝运行**。

    本脚本与执行器都会临时改仓库源码 ⇒ 并发会互相污染（坑区「预检与执行器并发」
    同族：并发读被改坏的源码会报出假结论）。执行器用 ``*.mutbak`` 备份 ⇒ 用它当信号。
    ⚠️ 残留 ``.mutbak`` 也可能是「执行器被中断」留下的（坑区「中断执行器也会留下
    残留」）⇒ 那种情况源码可能仍处于突变态 ⇒ **同样拒绝**（保守是对的）。
    """
    found: list[Path] = []
    for d in ("packages", "tests", "scripts", "database"):
        found += list((ROOT / d).rglob("*.mutbak"))
    if found:
        print(f"[ABORT] 检测到 {len(found)} 个 *.mutbak ⇒ 突变执行器可能在跑或上次被中断")
        for p in found[:5]:
            print(f"        {p.relative_to(ROOT)}")
        raise SystemExit(2)


def main() -> int:
    _assert_no_mutation_runner()
    src = TARGET.read_text(encoding="utf-8")
    if src.count(OLD) != 1:
        print(f"[ABORT] 旧形态命中 {src.count(OLD)} 次（要求恰好 1）")
        return 2
    before = sha(TARGET)
    shutil.copy2(TARGET, BAK)
    try:
        TARGET.write_text(src.replace(OLD, NEW), encoding="utf-8")
        print(f"[MUTATED] 已撤掉 _mask_profile_rows 里的掩码（{before[:12]} → {sha(TARGET)[:12]}）")
        for t in TESTS:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", t, "-q", "--no-header", "-p", "no:cacheprovider"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            status = "RED  [R]" if proc.returncode != 0 else "GREEN [G]"
            print(f"\n=== {status}  {t.split('::')[-1]} (exit={proc.returncode}) ===")
            tail = proc.stdout.strip().splitlines()[-14:]
            for line in tail:
                print("   " + line)
    finally:
        shutil.copy2(BAK, TARGET)
        BAK.unlink(missing_ok=True)
        after = sha(TARGET)
        ok = after == before
        print(f"\n[RESTORED] 还原校验 {'OK' if ok else 'FAILED'}（{after[:12]} vs {before[:12]}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
