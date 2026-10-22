"""推演面板「按选中章取 delta」的突变规格（2026-10-22，CI flaky 根因的判别力看守）。

事故
----
``apps/web/src/pages/SimulationPanel.test.tsx`` 的用例
「按 number 列出章节；切换章节 ⇒ 重新取该章 delta」在 **CI 上恒红**
（2026-10-21 两条 run：``39654459279`` / ``39655441184``，web job 失败），
而**本地全量 vitest 780 全绿**。

真因不是实现坏了，是**断言的观察时机错了**：

* ``chapterId`` 由「取章节列表」那个 effect 的 ``.then`` 落定
  ⇒ ``select`` 渲染出来时，**取 delta 的 effect（依赖 ``chapterId``）还没跑**；
* 那个 effect 是 React 的 **passive effect**（调度时机不定），
  且它内部还要 ``await`` 一层；
* 原断言是**同步**的 ``expect(listDeltas).toHaveBeenCalledWith('p1','ch_1')``
  ⇒ 抢在 effect 前面跑 ⇒ 本地（CPU 空闲、mock 立即 resolve）绿，
  CI（57 个文件并行、CPU 争抢、effect 调度被推后）红
  ⇒ CI 实测 ``Number of calls: 0``。

⇒ 修法 = 把那一条同步断言改成 ``await waitFor(...)``（**判别点不变**：仍要求
``listDeltas`` 以 ``('p1','ch_1')`` 被调用过）。

通用纪律
--------
**断言一个「发生在 effect 里的行为」时，观察点必须落在行为之后**——
「组件已经渲染出某个控件」不等于「该控件依赖的数据已经取过」：
数据取回是**另一次** effect 里的**异步**动作，与渲染之间没有 happens-before 关系。
同步断言挂在异步行为之后 ⇒ 变成**竞态断言**（本地绿、CI 红），是本仓
「夹具/断言的时机不对」一族的又一实例。

判别面
------
⚠️ **改断言形态必须重验判别力**（本仓纪律：判别面缩小是「看起来测到了」变
「测不到」的最常见路径）。故本规格钉两条突变，证明改后的 ``waitFor`` 版断言
**仍然**能抓住「不再按选中章取 delta」：

* **SIMDELTA-1** 把 ``listDeltas(projectId, chapterId)`` 改成
  ``listDeltas(projectId, 'ch_1')``（= 恒取第一章）⇒ 切到 ch_2 后不再重取
  ⇒ 用例的**后半句**必红（``toHaveBeenCalledWith('p1','ch_2')`` 永不满足）。
* **SIMDELTA-2** 撤掉「取 delta」那个 effect ⇒ 根本不取
  ⇒ 用例的**前半句**必红（``toHaveBeenCalledWith('p1','ch_1')`` 永不满足）。

两条各钉一个失效方向：**只钉其中一条不够**——只钉 SIMDELTA-1 的话，
「完全不取 delta」这种改法能全绿；只钉 SIMDELTA-2 的话，
「永远取第一章」这种改法能全绿。
"""

MUTATIONS = [
    (
        "SIMDELTA-1",
        "apps/web/src/pages/SimulationPanel.tsx",
        "      setDeltas(await storyStateApi.listDeltas(projectId, chapterId));\n",
        "      setDeltas(await storyStateApi.listDeltas(projectId, 'ch_1'));  # MUTATION-SIMDELTA-1: 恒取第一章\n",
        "src/pages/SimulationPanel.test.tsx",
    ),
    (
        "SIMDELTA-2",
        "apps/web/src/pages/SimulationPanel.tsx",
        "  useEffect(() => {\n    void loadDeltas();\n  }, [loadDeltas]);\n",
        "  // MUTATION-SIMDELTA-2: 撤掉取 delta 的 effect\n",
        "src/pages/SimulationPanel.test.tsx",
    ),
]
