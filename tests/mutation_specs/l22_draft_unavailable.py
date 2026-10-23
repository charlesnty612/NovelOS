"""「读不到草稿」必须 fail-closed：撤掉任一处 ⇒ 必红（critical C-1 / D-6，2026-10-23）。

缺陷形状
--------
``packages/domain/chapter/draft_resolver.py`` 的 ``resolve_draft`` 在**读库失败**时
返回 ``None``（与「该章没有草稿」**同一个返回值**）⇒ ``quality/service.py`` 拿到空正文
⇒ ``quality/scoring.py`` 的空正文分支给**满分 0 issue** ⇒ **该章从未被评估却拿到满分**，
提交门禁放行（2026-10-23 第二轮审查 critical C-1，Lead 已复现）。

本规格钉住**八处**「撤掉即失效」的机制点
--------------------------------------
* **DRAFT-UNAVAILABLE-1 / -2**：``resolve_draft`` 的两处失败分支改回 ``return None``
  ⇒ 「读失败」与「无草稿」再次同值 ⇒ 用例红（``DID NOT RAISE``）。
* **DRAFT-UNAVAILABLE-3**：``scoring.score_pacing`` 的「正文不可读」分支撤掉
  ⇒ 占位正文被当普通正文评分（pacing 得正分）⇒ 用例红。
* **DRAFT-UNAVAILABLE-4**：``scoring.score_style`` 的「正文不可读」分支撤掉
  ⇒ style 得正分 ⇒ 用例红。
* **DRAFT-UNAVAILABLE-5**：``quality/service.py`` 的「读失败 ⇒ 标记不可检」撤掉
  ⇒ ctx 退回空正文 ⇒ 报告又是满分 ⇒ 用例红。
* **DRAFT-UNAVAILABLE-6**：``chapter_commit/gate.py`` 的「不可检 ⇒ 一律硬停」撤掉
  ⇒ report 模式（含快通道）放行一个**从未被评估**的章 ⇒ 用例红。
* **DRAFT-UNAVAILABLE-7**（2026-10-23 对抗审查 A · T1-3 返工）：门禁里
  「不可检」的**独立错误类别**撤掉 ⇒ 退回「质量不合格」的落点（写
  ``revision_note`` / ``gate_blocked`` + 「补 plan_json / submit_delta」的错误建议）⇒ 用例红。
* **DRAFT-UNAVAILABLE-8**（同上）：门禁里「不可检 ⇒ 不产出改稿建议」的守卫撤掉
  ⇒ ``revision_guidance`` 里重新出现那条误导方向的建议 ⇒ 用例红。

⚠️ **全部是「只撤机制」**：replacement 不引入新的未定义名字、不破坏 import ⇒
红的**原因**是断言失效，而不是 ``NameError`` / ``ImportError``（后者会被误记成
「捕获」——AGENTS 坑区「变异改得太狠 ⇒ 归 INVALID 而不是 CAUGHT」）。

判别
----
``tests/unit/quality/test_draft_unavailable_fail_closed.py``（8 例：resolver 抛域异常 ×2 /
上下文标记 / 报告非满分 + 可见 issue / 门禁 enforce 阻断 / 门禁 report 模式也阻断 /
**「该章确实没有草稿」的反向不变量** / 正常路径）。
"""

MUTATIONS = [
    (
        "DRAFT-UNAVAILABLE-1",
        "packages/domain/chapter/draft_resolver.py",
        "        except sqlite3.Error as exc:\n"
        "            # 缺表 / 表损坏 / 查询异常 = **故障**：2026-10-23 critical C-1 / D-6——\n"
        "            # 此前这里返回 None（与「该章无草稿」同值）⇒ 上游把「没量到」当成\n"
        "            # 「量过了、没问题」⇒ 质量门给从未被评估的章节发满分 0 issue。\n"
        "            raise DraftResolutionError(\n"
        "                f\"读取草稿失败（chapter_id={chapter_id!r}）：{exc}\"\n"
        "            ) from exc\n",
        "        except sqlite3.Error:\n"
        "            return None\n",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
    (
        "DRAFT-UNAVAILABLE-2",
        "packages/domain/chapter/draft_resolver.py",
        "    except Exception as exc:  # noqa: BLE001 —— 打不开库 = **故障**，不是「无草稿」\n"
        "        raise DraftResolutionError(f\"无法打开数据库 {db_path}：{exc}\") from exc\n",
        "    except Exception:  # noqa: BLE001 —— 打不开库 → 读侧全容错，视为无草稿\n"
        "        return None\n",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
    (
        "DRAFT-UNAVAILABLE-3",
        "packages/core/quality/scoring.py",
        "    if is_draft_unavailable(draft):\n"
        "        return 0, [_draft_unavailable_issue(\"pacing\")]\n",
        "",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
    (
        "DRAFT-UNAVAILABLE-4",
        "packages/core/quality/scoring.py",
        "    if is_draft_unavailable(draft):\n"
        "        return 0, [_draft_unavailable_issue(\"style\")]\n",
        "",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
    (
        "DRAFT-UNAVAILABLE-5",
        "packages/core/quality/service.py",
        "    draft = (\n"
        "        DRAFT_UNAVAILABLE\n"
        "        if draft_unavailable\n"
        "        else ((resolved or {}).get(\"content\") or \"\")\n"
        "    )\n",
        "    draft = (resolved or {}).get(\"content\") or \"\"\n",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
    (
        "DRAFT-UNAVAILABLE-6",
        "packages/workflows/chapter_commit/gate.py",
        "    should_block = (\n"
        "        mode == \"enforce\" and (bool(blocking_issues) or confirm_unresolved)\n"
        "    ) or draft_unavailable\n",
        "    should_block = mode == \"enforce\" and (bool(blocking_issues) or confirm_unresolved)\n",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
    (
        "DRAFT-UNAVAILABLE-7",
        "packages/workflows/chapter_commit/gate.py",
        "        if draft_unavailable:\n"
        "            raise ValueError(\n"
        "                \"quality gate unavailable: 不可检 —— 草稿读不到（草稿表不可用），\"\n"
        "                \"本次评估未进行；这不是质量意见，请修复草稿表（drafts）后重跑\"\n"
        "            )\n",
        "",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
    (
        "DRAFT-UNAVAILABLE-8",
        "packages/workflows/chapter_commit/gate.py",
        "    if not draft_unavailable and (error_issues or gate_summary.confirm_issues):\n"
        "        revision_guidance = _build_revision_guidance(report, report.issues)\n",
        "    if error_issues or gate_summary.confirm_issues:\n"
        "        revision_guidance = _build_revision_guidance(report, report.issues)\n",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
]
