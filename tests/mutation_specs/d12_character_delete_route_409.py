"""D-12 路由侧「删除被引用角色 ⇒ 409」的突变规格（2026-10-23 · 第二轮审查 D-12）。

**执行**：由 Lead 统一跑 ``tests/mutation_specs/run_spec.py``；本文件只做只读预检
（``validate_specs.py``）。⚠️ **预检必须后置**——它读的正是执行器临时改写的源文件
（坑区「预检与执行器并发 ⇒ validate_specs 报出假『锚点未命中』」，2026-10-16 批次八）。

**背景**：迁移 0022 摘掉了 ``relationships`` 两端点的外键 ⇒ 删角色会留孤儿行，
而 ``PRAGMA foreign_key_check`` 报不出来。域层已修（``domain/character/service.py::delete``
查两端点引用 ⇒ 抛 ``ReferencedError``）。**但路由层没接** ⇒ 异常冒泡成 **500**
⇒ 把「可解释的引用冲突」报成「服务器炸了」。

本组守的正是**路由层那三行映射**（域层那条由 ``t7_character_delete_and_layering.py`` 守）。

一条
--------------------------------------------------------------------------
D12M1 撤掉 ``except ReferencedError → 409`` 映射
       ⇒ ``test_delete_referenced_character_returns_409_and_keeps_character`` 红
         （实际会变成 500）。

⚠️ 与域层规格的分工：``t7_character_delete_and_layering.py`` 守的是**域层不删任何东西**
（本组撤路由映射时域层仍会抛错、仍不删角色 ⇒ 那条域层用例**不会红**）⇒
两处必须各有一条规格，缺一就会出现「只修了一半也全绿」。

三条自查（写完必做，勿跳过）
--------------------------
1. **锚点恰好命中 1 次**——逐条 ``str.count`` 核过（= 1）；
   ``characters.py`` 工作树为 **LF**（该文件未被 autocrlf 转换），
   但执行器用 ``read_text(encoding="utf-8")``（universal newlines）读取、
   ``write_text`` 写入 ⇒ 锚点里的 ``\\n`` 两种情况都能命中。
2. **每条都真的改行为**——``run_spec.py`` 比对写入前后 SHA256，没变即 ``[ABORT]``。
   本条是**真换语义**（409 → 异常裸冒泡 ⇒ 500），不是「插了行注释、逻辑未变」那种自伤写法。
3. **交接前清点突变残留**——``find . -name "*.mutbak"`` + ``grep -rn "MUTATION-PROBE-D12"``
   （区分大小写；``MUTATION`` 本身是**正常测试函数名**的一部分，用裸词搜会误报）。
"""

MUTATIONS = [
    (
        # 撤掉路由层的 409 映射 ⇒ 被引用删除变成 500
        "D12M1",
        "packages/core/api/routers/characters.py",
        """    except ReferencedError as exc:
        # 角色被 relationships 引用 ⇒ 409（同 world 域口径，见 worlds.py::_handle_world_error）。
        # ⚠️ 漏掉它会变成 **500**：把「可解释的引用冲突」报成「服务器炸了」，
        # 客户端无法区分「重试有用」与「永久失败」。
        raise HTTPException(status_code=409, detail=str(exc)) from exc""",
        """    except ReferencedError:  # MUTATION-PROBE-D12M1 撤掉 409 映射
        raise""",
        "tests/api/test_character_delete_conflict.py",
    ),
]
