"""chapter_commit 的缓存失效失败：撤掉 warning ⇒ 必红（dev-b 批次二十二 P2-4）。

缺陷形状
--------
``packages/workflows/chapter_commit/commit.py`` 的 ``_commit_node`` 在 commit 成功后
显式失效本章装配缓存（兜底）。改造前收尾是 ``except Exception: pass`` ⇒
**失效失败零痕迹** ⇒ 失效没生效时，该章后续装配**静默命中旧缓存**（脏命中）而
无人知道。

突变
----
* **COMMIT-INVAL-1** 把 ``log.warning`` 降成 ``log.debug`` ⇒ 失效失败**不再留
  warning** ⇒ ``test_cache_invalidation_failure_still_commits_but_leaves_a_warning``
  必红（它断言「commit 仍成功」**且**「有 warning 记录」）。

⚠️ **replacement 不破坏行为**（仍是同一处 except、仍不抛、仍不阻断 commit）⇒
红的**原因**是「warning 没了」这个断言失效，而不是别的异常——这正是本条要守的
机制（AGENTS 坑区「断言了结果 ≠ 判别了机制」：本条断言的是**留痕**本身）。

判别
----
``tests/unit/test_ctx_b_commit_cache_invalidate_warning.py``（2 例：失效失败 ⇒ commit
仍成功 + 有 warning / 正常路径 ⇒ 不得有该 warning）。
"""

MUTATIONS = [
    (
        "COMMIT-INVAL-1",
        "packages/workflows/chapter_commit/commit.py",
        "    except Exception as exc:  # noqa: BLE001 —— 失效失败不阻断 commit，但**必须留痕**\n"
        "        _log.warning(\n",
        "    except Exception as exc:  # noqa: BLE001 —— 失效失败不阻断 commit，但**必须留痕**\n"
        "        _log.debug(\n",
        "tests/unit/test_ctx_b_commit_cache_invalidate_warning.py",
    ),
]
