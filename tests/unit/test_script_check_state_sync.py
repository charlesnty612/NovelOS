"""``scripts/check_state_sync.py`` 的**判据强度口径**必须写在案（dev-b 批次二十二 P1-2）。

背景
----
该脚本的判据是**实体 id 集合**的双向差集（DB 侧 ``fetch_db_ids``，快照侧同样只抽
id 集合）⇒ 它**发现不了「实体集一致、但某个实体的内容已错」**。而输出只有一句
``SYNC OK`` ⇒ 读者容易把它当「内容正确性」的证据。

批次二十二已把这条口径写进脚本 docstring（新增「判据强度：``SYNC OK`` 能证明什么、
不能证明什么」一节）。本文件把那节**变成可执行的口径看守**：

- 口径只写在**散文**里 ⇒ 下一次有人「顺手精简 docstring」就悄悄没了
  （同 P0-4 的教训：口径只活在文档里、代码里没写）；
- ⇒ 本用例断言 docstring **必须含那几个关键词**（**按关键词断言，不按整句断言**
  ⇒ 允许日后改写措辞，只是不许把口径删掉）。

⚠️ **本文件只做「正向关键词」断言，不做「不得含某短语」的断言**：后者会自伤
（文档里写「不校验实体内容」时，「校验实体内容」这个子串**天然存在** ⇒ 任何
「不得含」清单都会误报；同 AGENTS 坑区「正则扫源码计数类断言只能用下界」的精神：
宁可宽松，不可自伤）。

零真实 LLM、零内容仓接触、零 dev 库、**零不可逆操作**（只读脚本文件）。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_state_sync.py"


def _docstring() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith('"""'), "脚本应以模块 docstring 开头"
    end = text.index('"""', 3)
    return text[3:end]


def test_docstring_states_that_sync_ok_is_about_membership_not_content():
    """docstring 必须写明：判据是**集合成员关系**，``SYNC OK`` **≠** 内容正确。

    判别按**关键词**（不是整句）⇒ 允许日后改写措辞，只是不许把口径删掉。
    """
    doc = _docstring()

    for keyword in ("集合成员关系", "不校验", "实体内容", "SYNC OK"):
        assert keyword in doc, (
            f"模块 docstring 必须含「{keyword}」——判据强度口径不许只活在散文里"
        )
