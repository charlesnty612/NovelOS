"""codegen 产物漂移闸门（``--check``）的看守。

背景（2026-10-23 批次三十，台账 C-2）：``scripts/export_openapi.py`` 与
``scripts/gen_frontend_types.py`` 有 ``--out`` 但**没有** ``--check``；两个产物可被
重新生成校验，但只能靠人工 regen + diff 发现漂移。

⚠️ **既有覆盖的精确边界（别夸大「零看守」）**：``tests/api/test_openapi_contract.py``
已有**集合级**看守（``info.version`` 一致 / ``paths`` 集合相等 /
``components.schemas`` 名集合相等）。实测它的判别力边界：把
``AdoptRequest.content`` 的 ``type`` 由 ``string`` 改成 ``number``（**名字集合完全不变**）
⇒ 那三条集合断言**全部通过**。即：**字段级漂移（已有 schema 内部的类型 / 可选性 /
enum / 描述变化）此前无任何机器看守**。

本文件补的正是这一段：**全文级**（逐字比对、换行归一）增量，外加一个可复用于
CI / pre-commit 的 CLI 闸门（``--check``）。

本文件钉住七件事：
1. 仓库内两份产物与「重新生成结果」一致（干净树必须绿）；
2. 产物被改一个字节 ⇒ 判不一致（闸门有判别力；恒真的闸门会在这里红）；
3. **换行差异不算漂移**——本仓无 ``.gitattributes`` 且本机 ``core.autocrlf=true``：
   git blob 存 LF、Windows 工作树是 CRLF、``Path.write_text`` 也会翻成 CRLF。
   若逐字节比对，这道闸门会在「不同平台 / 不同 git 配置」下给出**不同答案**
   （那是环境假信号，不是 schema 漂移）⇒ 比对前必须换行归一；
4. ``--check`` **绝不写产物盘**（它只是校验）；
5. **CLI 层的退出码**（2026-10-23 批次三十一，D-8）：漂移 ⇒ **返回码非 0**。
   ⚠️ 此前全仓 ``grep -rn '"--check"' tests/`` 只命中两个辅助函数，二者只对
   **已同步的仓内产物**调用 ⇒ 只断言 ``== 0``、**从不断言 ``!= 0``**
   ⇒ 把 ``export_main`` / ``gen_types_main`` 换成**恒返回 0** 的替身时
   8 条用例全绿（无一抓到）。而 CLI 正是 CI / pre-commit 要消费的那一层。
6. **漂移路径下的无写性**：产物 **bytes + mtime 逐位未变**（此前只测了绿路径）；
7. **默认路径锚定仓库根**（D-8 同批 3-3）与**临时目录不泄漏**（3-2）：
   ``--check`` 每次调用曾泄漏一个空目录（实测系统临时目录已积 208 个），
   且默认路径 CWD 相对 ⇒ 在非仓库 CWD 执行会得到**假漂移**。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from scripts import _codegen_check
from scripts.export_openapi import main as export_main
from scripts.gen_frontend_types import main as gen_types_main

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI = REPO_ROOT / "apps" / "web" / "openapi.json"
TYPES = REPO_ROOT / "apps" / "web" / "src" / "api" / "types.generated.ts"


def _export_check() -> int:
    # 显式传绝对路径 ⇒ 不依赖 pytest 的 CWD
    return export_main(["--check", "--out", str(OPENAPI)])


def _types_check() -> int:
    return gen_types_main(
        ["--check", "--openapi", str(OPENAPI), "--out", str(TYPES)]
    )


def test_repo_openapi_artifact_matches_regeneration():
    """干净树：重新生成结果与仓内 ``openapi.json`` 一致（不一致即漂移，须 regen 后提交）。"""
    assert _export_check() == 0


def test_repo_frontend_types_artifact_matches_regeneration():
    """干净树：``types.generated.ts`` 与重新生成结果一致。"""
    assert _types_check() == 0


def test_check_does_not_write_artifact():
    """``--check`` 只校验、不写盘：跑完产物字节不变。"""
    before = OPENAPI.read_bytes()
    assert _export_check() == 0
    assert OPENAPI.read_bytes() == before


@pytest.mark.parametrize("artifact", [OPENAPI, TYPES], ids=["openapi", "types"])
def test_drift_is_detected(artifact: Path, tmp_path: Path):
    """产物多一行 ⇒ 判不一致（闸门有判别力；「恒真」的实现会在这里红）。"""
    text = artifact.read_text(encoding="utf-8")
    copy = tmp_path / artifact.name
    copy.write_text(text + "\n// drift\n", encoding="utf-8")

    ok, msg = _codegen_check.check_artifact(copy, text)

    assert ok is False
    assert "DRIFT" in msg


def test_line_ending_difference_is_not_drift(tmp_path: Path):
    """CRLF ↔ LF 不算漂移（否则闸门会随平台 / ``core.autocrlf`` 变脸）。

    用合成的两行文本，**显式构造「产物是 CRLF、期望是 LF」**——这正是
    Windows(``core.autocrlf=true``) 与 Linux 之间的真实差异。
    按字节写盘，绕开 text mode 的 universal-newline 转换（否则构造不出 CRLF 产物）。
    撤掉换行归一时本用例必红（不是等价突变）。
    """
    lf = "line1\nline2\n"
    crlf = lf.replace("\n", "\r\n")
    artifact = tmp_path / "artifact.txt"
    artifact.write_bytes(crlf.encode("utf-8"))

    # 前提断言：产物确实带 CRLF（否则本用例是空的）
    assert b"\r\n" in artifact.read_bytes()

    ok, msg = _codegen_check.check_artifact(artifact, lf)

    assert ok is True, msg


def test_missing_artifact_is_drift(tmp_path: Path):
    """产物缺失也算不一致（不能让「删掉产物」蒙混过关）。"""
    ok, msg = _codegen_check.check_artifact(tmp_path / "nope.json", "{}")

    assert ok is False
    assert "MISSING" in msg


# ---------------------------------------------------------------------------
# D-8（2026-10-23 批次三十一）：**CLI 层的退出码**此前全仓无看守
# ---------------------------------------------------------------------------


def test_export_check_returns_nonzero_on_drift(tmp_path: Path):
    """漂移 ⇒ **CLI 返回码非 0**（CI / pre-commit 消费的就是这一层）。

    ⚠️ 断言打在**实际返回码**上，不是「函数被调用」。
    """
    drifted = tmp_path / "openapi.json"
    drifted.write_text(OPENAPI.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")

    rc = export_main(["--check", "--out", str(drifted)])

    assert rc != 0, "漂移时 CLI 竟返回 0 ⇒ 闸门在 CI 层形同虚设"


def test_gen_types_check_returns_nonzero_on_drift(tmp_path: Path):
    """同上（``gen_frontend_types.py`` 那一侧）。"""
    drifted = tmp_path / "types.generated.ts"
    drifted.write_text(TYPES.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")

    rc = gen_types_main(["--check", "--openapi", str(OPENAPI), "--out", str(drifted)])

    assert rc != 0, "漂移时 CLI 竟返回 0 ⇒ 闸门在 CI 层形同虚设"


def test_drift_path_leaves_artifact_bytes_and_mtime_unchanged(tmp_path: Path):
    """漂移路径下产物 **bytes + mtime 逐位未变**（此前只测了绿路径的无写性）。"""
    drifted = tmp_path / "openapi.json"
    drifted.write_text(OPENAPI.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")
    before_bytes = drifted.read_bytes()
    before_mtime = drifted.stat().st_mtime_ns

    assert export_main(["--check", "--out", str(drifted)]) != 0

    assert drifted.read_bytes() == before_bytes
    assert drifted.stat().st_mtime_ns == before_mtime


# ---------------------------------------------------------------------------
# 3-2 / 3-3（2026-10-23 批次三十一）：临时目录泄漏 + 默认路径 CWD 相对
# ---------------------------------------------------------------------------


def test_check_does_not_leak_temp_dir(monkeypatch, tmp_path: Path):
    """``--check`` 不得留下临时目录（旧实现每次调用泄漏一个空目录）。

    ⚠️ 旧实现的 ``tempfile.mkdtemp(prefix="novelos_openapi_")`` **从不清理**
    ⇒ 实测系统临时目录已积 208 个空目录、每次 ``--check`` +1
    ⇒ CHANGELOG 的「绝不写盘」**字面为假**（准确表述是「绝不写**产物**盘」）。
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    before = {p.name for p in tmp_path.iterdir()}

    assert export_main(["--check", "--out", str(OPENAPI)]) == 0

    leaked = {p.name for p in tmp_path.iterdir()} - before
    assert leaked == set(), f"泄漏了临时目录：{sorted(leaked)}"


def test_default_paths_are_anchored_to_repo_root(monkeypatch, tmp_path: Path):
    """默认路径必须相对**仓库根**（不是 CWD）⇒ 任意 CWD 执行都指向同一产物。

    ⚠️ 旧实现默认 ``Path("apps/web/openapi.json")``（CWD 相对）⇒ 在非仓库 CWD
    执行会得到「产物不存在」的**假漂移**（export → MISSING exit 1 / gen → exit 2），
    而该 CLI 的定位是「可复用于 CI / pre-commit」⇒ 假漂移 = CI 随机红。
    """
    monkeypatch.chdir(tmp_path)

    assert export_main(["--check"]) == 0
    assert gen_types_main(["--check"]) == 0


def test_missing_repo_root_gives_clear_error_not_vague_missing(
    monkeypatch, tmp_path: Path, capsys
):
    """找不到仓库根 ⇒ **明确文案** + 非 0 退出，而不是含糊的 ``MISSING`` 假漂移。"""
    monkeypatch.setattr(_codegen_check, "repo_root", lambda: tmp_path / "not-a-repo")

    rc = export_main(["--check"])

    assert rc != 0
    captured = capsys.readouterr()
    assert "仓库根" in captured.out + captured.err


def test_missing_repo_root_gives_clear_error_for_gen_types(
    monkeypatch, tmp_path: Path, capsys
):
    """同上（``gen_frontend_types.py`` 那一侧）。"""
    monkeypatch.setattr(_codegen_check, "repo_root", lambda: tmp_path / "not-a-repo")

    rc = gen_types_main(["--check"])

    assert rc != 0
    captured = capsys.readouterr()
    assert "仓库根" in captured.out + captured.err


def test_field_level_drift_is_detected_where_set_based_guard_is_blind(tmp_path: Path):
    """字段级漂移必须被抓到——**这正是集合级看守的盲区**（本文件存在的理由）。

    既有 ``tests/api/test_openapi_contract.py`` 只比 ``info.version`` / ``paths`` /
    ``components.schemas`` **名集合**。把某个已存在属性的 ``type`` 改掉时，这三条
    集合断言全部通过（实测：``AdoptRequest.content`` 由 ``string`` 改 ``number``）。
    本闸门必须对此报警，否则它没有增量价值。
    """
    original = OPENAPI.read_text(encoding="utf-8")
    document = json.loads(original)
    schemas = document["components"]["schemas"]

    target: tuple[str, str] | None = None
    for schema_name, schema in schemas.items():
        for prop, pschema in (schema.get("properties") or {}).items():
            if pschema.get("type") == "string" and not pschema.get("enum"):
                pschema["type"] = "number"
                target = (schema_name, prop)
                break
        if target:
            break
    assert target is not None, "openapi 里找不到 string 属性——本用例的突变前提需更新"

    # 前提断言：名字集合**完全不变** ⇒ 集合级看守看不见这次改动
    assert set(schemas) == set(json.loads(original)["components"]["schemas"])
    assert set(document["paths"]) == set(json.loads(original)["paths"])

    mutated = tmp_path / "openapi.json"
    mutated.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, msg = _codegen_check.check_artifact(mutated, original)

    assert ok is False
    assert "DRIFT" in msg
