"""codegen 产物漂移校验的共用件（``export_openapi`` / ``gen_frontend_types`` 共用）。

**只做三件事：换行归一、首个差异摘要、产物比对。不写任何文件。**

⚠️ 比对口径必须在两处**完全一致**，否则「同一个闸门」对两份产物会给出不同的判定
——这正是本仓坑区反复出现的「两套实现口径分叉」形状。故收敛到这里单点。

⚠️ **必须换行归一**：本仓无 ``.gitattributes``，且本机 ``core.autocrlf=true``
⇒ git blob 里存 LF、Windows 工作树是 CRLF、``Path.write_text`` 在 Windows 上也会把
``\\n`` 翻成 ``\\r\\n``。不归一的话，闸门会在「不同平台 / 不同 core.autocrlf 设置」下
给出**不同答案**——那是环境依赖的假信号，不是 schema 漂移。**闸门只该对漂移报警。**

⚠️ **产物必须按「字节」读、不能用 ``Path.read_text``**：``read_text`` 走 text mode，
``newline=None`` 会做 universal-newline 转换，读进内存时 CRLF 已被翻成 LF ⇒
换行归一变成永远不生效的死代码，而**行为上看起来一切正常**（Windows 上照样绿）。
本仓踩过一次：把归一改成恒等（等价突变）后，看守用例仍然全绿 ⇒ 说明它在测的不是
它声称的东西。故这里用 ``read_bytes().decode(...)``，让归一**真正承重**：
撤掉它，CRLF 产物会立刻被判 DRIFT（``test_line_ending_difference_is_not_drift`` 变红）。
"""
from __future__ import annotations

from pathlib import Path


def normalize_newlines(text: str) -> str:
    """换行归一（CRLF → LF）。"""
    return text.replace("\r\n", "\n")


def diff_summary(expected: str, actual: str) -> str:
    """首个差异行 / 行数差异摘要（诊断用，不输出完整 diff）。"""
    exp = normalize_newlines(expected).splitlines()
    act = normalize_newlines(actual).splitlines()
    if len(exp) != len(act):
        return f"行数不同：重新生成 {len(exp)} 行 vs 仓内产物 {len(act)} 行"
    for i, (e, a) in enumerate(zip(exp, act), 1):
        if e != a:
            return (
                f"首个差异在第 {i} 行：\n"
                f"    重新生成: {e[:200]}\n"
                f"    仓内产物: {a[:200]}"
            )
    return "文本一致但字节不同（疑似仅编码差异）"


def check_artifact(path: Path, rendered: str) -> tuple[bool, str]:
    """校验产物与给定渲染结果是否一致（换行归一后比较）。

    返回 ``(是否一致, 摘要)``。**不写任何文件**；产物缺失视为不一致。
    """
    if not path.exists():
        return False, f"MISSING：产物不存在：{path}"
    # 按字节读再显式解码：read_text 的 universal-newline 转换会悄悄把 CRLF 变 LF，
    # 于是下面的归一变成死代码（见模块 docstring 的踩坑记录）。
    actual = path.read_bytes().decode("utf-8")
    if normalize_newlines(actual) == normalize_newlines(rendered):
        return True, f"OK：{path} 与重新生成结果一致"
    return False, f"DRIFT：{path} 与重新生成结果不一致\n{diff_summary(rendered, actual)}"
