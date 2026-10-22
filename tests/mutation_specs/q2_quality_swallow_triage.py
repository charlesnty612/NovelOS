r"""质量域「吞异常分诊」B 类留痕的突变规格（2026-10-22 批次二十三 · novelos-dev-a）。

补的缺口
--------
批次二十三分诊在质量域 + 领域层共判出 **8 处 B 类**（吞掉后导致静默降级 / 静默跳过 /
静默用默认值，而调用方不知情），已逐处加 ``log.warning`` 留痕（**控制流不变**）。
按方法论要求，**新测试必须做突变验证**——否则「这些用例到底能不能判别」无人知道。

两条突变各撤掉**一处**留痕
--------------------------
* **QSWALLOW-1**——撤掉 ``quality/service.py::_read_chapter_plan`` 里那条
  ``log.warning`` ⇒ ``tests/unit/quality/test_quality_swallow_triage.py``
  的「脏 plan_json 被留痕」用例**必红**（它断言「恰好 1 条记录」⇒ 撤掉后为 0 条）。
* **QSWALLOW-2**——撤掉 ``domain/character/service.py::_load_aliases`` 里那条
  ``log.warning`` ⇒ ``tests/unit/test_quality_domain_swallow_triage.py`` 的
  「脏 aliases 被留痕」用例**必红**。

两条都是「**只改日志、不改控制流**」⇒ 被测行为（仍不抛、仍按原语义降级）**一字不变**
⇒ 红的**只可能**是「日志留痕」这条断言 ⇒ 判别点专属。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① 撤掉 quality 侧的一处留痕 ⇒ 「有日志记录」断言必红
    # ------------------------------------------------------------------
    (
        "QSWALLOW-1-quality-plan-json-warning-removed",
        "packages/core/quality/service.py",
        '        log.warning(\n'
        '            "chapters.plan_json 解析失败 ⇒ 退化为空 plan（评估按「无 plan」算）："\n'
        '            "chapter_id=%s err=%s",\n'
        '            chapter_id,\n'
        '            exc,\n'
        '        )',
        "        pass  # MUT: 撤掉留痕（控制流不变）",
        "tests/unit/quality/test_quality_swallow_triage.py",
    ),
    # ------------------------------------------------------------------
    # ② 撤掉 domain 侧的一处留痕 ⇒ 「有日志记录」断言必红
    # ------------------------------------------------------------------
    (
        "QSWALLOW-2-domain-character-aliases-warning-removed",
        "packages/domain/character/service.py",
        '        log.warning(\n'
        '            "entities.aliases 解析失败 ⇒ 别名退化为空列表（角色别名静默丢失）：err=%s",\n'
        '            exc,\n'
        '        )',
        "        pass  # MUT: 撤掉留痕（控制流不变）",
        "tests/unit/test_quality_domain_swallow_triage.py",
    ),
    # ------------------------------------------------------------------
    # ③ 撤掉 domain 侧 volume 的留痕 ⇒ 用例必红
    #    （第 9 处 B 类：Lead 看过完整清单后**显式批准**）
    # ------------------------------------------------------------------
    (
        "QSWALLOW-3-volume-terminal-snapshot-warning-removed",
        "packages/domain/volume/service.py",
        '                log.warning(\n'
        '                    "volumes.terminal_snapshot_json 解析失败 ⇒ 退化为 None"\n'
        '                    "（与「列本身为空」同形）：volume_id=%s err=%s",\n'
        '                    d.get("volume_id"),\n'
        '                    exc,\n'
        '                )',
        "                pass  # MUT: 撤掉留痕（控制流不变）",
        "tests/unit/test_quality_domain_swallow_triage.py",
    ),
]
