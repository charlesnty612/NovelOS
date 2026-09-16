"""正文规整与内部标识检出（确定性算子；纯函数——不读库、不读环境、不打日志）。

**为什么有本模块**（实证来源：2026-09-16 新书01 全弧 24 章 run）：

1. **对话引号跨章漂移**：同一本书逐章实测——16 章用「」（ch1~5 / 7 / 12~15 / 19~24）、
   3 章用 “”（ch6 / 10 / 18）、5 章用英文直引号 ``"``（ch8 / 9 / 11 / 16 / 17）；
   ch22 / ch24 还在「」章里掺了直引号对（单章内混用）。文风锚点（榜一）用
   “”（U+201C/U+201D），故规整目标 = 弯双引号。跨章漂移靠提示词管不住。
2. **内部字段名漏进正文**：ch2 v2 实测「他盯着桌上那盏快灭的灯，忽然想起
   recalled_passages 里自己的那句话」——改稿轮把上下文键名写进了正文。这类
   **不能自动删**（删了句子就断），只检出并报告，交人工或返修流程。

**抽样核实**（项目纪律：算子在采信前先抽样检视命中实例）：两条算子在 119 份 draft
（新书01 全弧）上全量回放，逐行看 diff：

- 引号算子：109 份有变更，归一化「」2924 对 + 直引号 643 对；**保持原样**的行有两类——
  6 行「」不配对（形状：``「不知道。」陈来娣…「——我听说…」``，跨段开引号）、
  2 行直引号（``"旧档副本的擦边球。"林策说，"B级业务权限够用…"``，对话以 ASCII 字母
  起头，判不出开/闭方向）。两类都是本模块设计内的「判不出 → 不动」。
- 幂等：全量二次规整零变更、零残留。
- 标识算子：全量 distinct 命中仅 1 个（即上面的 recalled_passages），零误报。

用例语料取自该语料，见 ``tests/unit/quality/test_normalize.py``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 目标形态：弯双引号（与文风锚点一致，U+201C 开 / U+201D 闭）。
_LQUOTE = "\u201c"
_RQUOTE = "\u201d"

# 变更清单里最多带几条样例（够人工定位即可，不把整章塞进报告）。
_MAX_SAMPLES = 3


@dataclass(frozen=True)
class _QuoteSource:
    """一种源引号形态 → 目标 “” 的映射。

    ``symmetric``：开闭同形（只有英文直引号如此）——方向按出现次序判定，且每个引号要过
    「引号内以中文起头、前一字符不是 ASCII 字母数字」的门；角括号开闭符不同，出现次序即
    方向，无歧义。
    """

    label: str
    open_ch: str
    close_ch: str
    symmetric: bool = False


# 处理顺序即变更清单登记顺序（固定顺序 → 报告可比对）。
_QUOTE_SOURCES: tuple[_QuoteSource, ...] = (
    _QuoteSource("「」", "\u300c", "\u300d"),
    _QuoteSource('"', '"', '"', symmetric=True),
)

# JSON 片段行（键值形态）与代码围栏内的行不参与规整：正文以外的引号不是对话。
_JSON_KEY_RE = re.compile(r'"[A-Za-z_][A-Za-z0-9_]*"\s*:')
_FENCE_PREFIXES = ("```", "~~~")

# 「内侧相邻中文」的判定范围：汉字 + 中文标点/全角形式。
_CHINESE_RANGES = (
    (0x3000, 0x303F),  # CJK 符号与标点：。、 「」 《》 等
    (0x3400, 0x4DBF),  # 汉字扩展 A
    (0x4E00, 0x9FFF),  # 汉字基本区
    (0xF900, 0xFAFF),  # 汉字兼容区
    (0xFF00, 0xFFEF),  # 全角形式：！？：；（）等
)
# 通用标点区里中文正文常用者（破折号/省略号/弯引号），单独列出避免扩大整段区间。
_CHINESE_PUNCT_EXTRAS = frozenset("—–…‘’“”")


def _is_chinese(ch: str) -> bool:
    """该字符算不算「中文正文」（汉字或中文标点）。空串返回 False。"""
    if not ch:
        return False
    if ch in _CHINESE_PUNCT_EXTRAS:
        return True
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CHINESE_RANGES)


def _pair_positions(line: str, src: _QuoteSource) -> list[tuple[int, int]] | None:
    """本行该引号族的配对位置表；无法**严格**配对时返回 ``None``（调用方整行不动）。

    判定（安全边界，宁可不改）：
    - 角括号：出现次序即开/闭；同族嵌套（``「他说。「再来一次。」」``）或落单闭符
      一律判失败。
    - 直引号（开闭同形）：方向只能按出现次序判定，所以每个引号都得「像对话」才敢配对——
      处于**待配对**状态时，要求其后一字符是中文（引号内以中文正文起头），且前一字符不是
      ASCII 字母/数字（``27"`` / ``5'11"`` / ``abc"`` 的引号前都是字母数字）；已被**配对中**
      的引号一律收尾（引号内以数字收尾是正常的：``"待穿局工单：3"``）。
      任何一处判不出方向（英制符号、英文对白 ``"hello"``、缩写）→ **整行不动**：不做
      「跳过该引号、继续配对后面的」，那会让后面的引号错位配对（实测风险形状：
      ``他说，"B级够用。"她答，"C级也行，"就走了。``——跳过 ``"B`` 那个开引号后，
      ``够用。`` 后面的闭引号会被当成开引号）。
    - 行末仍处于未配对状态 → 判失败。
    """
    pairs: list[tuple[int, int]] = []
    pending: int | None = None
    for i, ch in enumerate(line):
        if ch != src.open_ch and ch != src.close_ch:
            continue
        if not src.symmetric:
            if ch == src.open_ch:
                if pending is not None:
                    return None
                pending = i
            else:
                if pending is None:
                    return None
                pairs.append((pending, i))
                pending = None
            continue
        if pending is None:
            nxt = line[i + 1] if i + 1 < len(line) else ""
            prev = line[i - 1] if i else ""
            if not _is_chinese(nxt) or (prev.isascii() and prev.isalnum()):
                return None
            pending = i
        else:
            pairs.append((pending, i))
            pending = None
    if pending is not None:
        return None
    return pairs


def _target_form_ok(line: str) -> bool:
    """变换后的目标形态必须严格成对（无嵌套、无落单）。"""
    pending = False
    for ch in line:
        if ch == _LQUOTE:
            if pending:
                return False
            pending = True
        elif ch == _RQUOTE:
            if not pending:
                return False
            pending = False
    return not pending


def _apply_pairs(line: str, pairs: list[tuple[int, int]]) -> str:
    chars = list(line)
    for open_i, close_i in pairs:
        chars[open_i] = _LQUOTE
        chars[close_i] = _RQUOTE
    return "".join(chars)


def _normalize_line(line: str) -> tuple[str, list[tuple[_QuoteSource, list[tuple[int, int]]]]]:
    """规整单行；返回 ``(行文本, [(源形态, 配对位置), ...])``。"""
    found: list[tuple[_QuoteSource, list[tuple[int, int]]]] = []
    for src in _QUOTE_SOURCES:
        pairs = _pair_positions(line, src)
        if pairs:
            found.append((src, pairs))
    if not found:
        return line, []
    out = line
    for _src, pairs in found:
        out = _apply_pairs(out, pairs)
    # 跨族嵌套（如「他说。"你好"」）变换后会得到嵌套的 “…”：目标形态本身就不合法，
    # 整行回退——宁可少改，不留歧义文本。
    if not _target_form_ok(out):
        return line, []
    return out, found


def normalize_prose(text: str) -> tuple[str, list[dict[str, Any]]]:
    """确定性正文规整。返回 ``(规整后文本, 变更清单)``。

    只做**安全**变换——即「改了之后语义不可能变」的那些：

    1. 成对出现的 ``「…」`` → ``“…”``（角括号对与锚点的弯双引号同义）。
    2. 成对的英文直引号 ``"…"`` → ``“…”``：引号内以中文正文起头（后一字符是汉字/中文
       标点）、且引号前不是 ASCII 字母数字时才认作对话；一处判不出方向（``27"``、
       ``"hello"``、``5'11"`` 等）→ **整行不动**。
    3. 已是 ``“…”`` 的文本零变更（目标形态即现状）。

    **不做**（归风格算子 / 人工）：

    - 不配对、同族嵌套（``「他说。「再来一次。」」``）、落单的引号：该行该族**逐字不动**；
    - 代码围栏（````` / ``~~~``）内的行、JSON 键值形态行（``"key":``）：整行不动；
    - ``『』`` / ``《》`` / 破折号 / 单引号：不在本模块职责内。

    行结构（换行、段落、空白）原样保留：只有引号字符被替换。

    变更清单按源形态登记，形如::

        [{"rule": "NORM-QUOTE", "source": "「」", "count": 2, "samples": ["「走吧。」→“走吧。”"]}]

    ``count`` 为归一化的**引号对数**，``samples`` 最多 :data:`_MAX_SAMPLES` 条
    （``原→新`` 形式）；无变更返回 ``[]``。空串 / 纯空白 / 超短文本安全返回。
    """
    if not text:
        return text, []
    counts: dict[str, int] = {src.label: 0 for src in _QUOTE_SOURCES}
    samples: dict[str, list[str]] = {src.label: [] for src in _QUOTE_SOURCES}
    in_fence = False
    out_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(_FENCE_PREFIXES):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence or _JSON_KEY_RE.search(line):
            out_lines.append(line)
            continue
        new_line, found = _normalize_line(line)
        out_lines.append(new_line)
        for src, pairs in found:
            counts[src.label] += len(pairs)
            room = max(0, _MAX_SAMPLES - len(samples[src.label]))
            for open_i, close_i in pairs[:room]:
                samples[src.label].append(
                    f"{line[open_i : close_i + 1]}→{new_line[open_i : close_i + 1]}"
                )
    changes = [
        {
            "rule": "NORM-QUOTE",
            "source": src.label,
            "count": counts[src.label],
            "samples": samples[src.label],
        }
        for src in _QUOTE_SOURCES
        if counts[src.label]
    ]
    return "\n".join(out_lines), changes


# snake_case（≥2 段）内部标识：payload 键名 / 上下文键名 / 环境变量名的最小公共形态。
# 不含 camelCase（iPhone / AiStation 是正常写法，不是泄漏）；无下划线不命中（OK / PPT）。
_INTERNAL_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+")


def find_internal_identifiers(text: str) -> list[str]:
    """检出正文里的内部标识（去重、保序）。

    命中形态：``[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+``——两段以上下划线的
    snake_case 标识（``recalled_passages`` / ``story_state`` / ``chapter_goal`` /
    ``NOVELOS_INSTANCE_ID``）。这类标识是软件层的名字，出现在正文里即「内部字段名
    漏进正文」事故；**本函数只报不修**（删掉标识会让句子断掉，处置权归人工/返修）。

    不命中：单个英文词（``OK`` / ``PPT``）、camelCase（``iPhone`` / ``AiStation``）、
    中文、纯数字——即正常中文写作里会出现的英文片段一律不报。
    空串 / 纯空白返回 ``[]``。纯函数：不读库、不读环境、不打日志。
    """
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for match in _INTERNAL_IDENTIFIER_RE.finditer(text):
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


__all__ = ["find_internal_identifiers", "normalize_prose"]
