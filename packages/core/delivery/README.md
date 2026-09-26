# 交付判定（delivery）

> 「这一章 / 这本书能不能交？」的**单一出口**。纯规则聚合 + 纯函数推导，无 LLM、
> 无新依赖、不改任何表结构（只读既有 ``quality_reports`` / ``chapters`` / ``drafts``）。
> 诞生背景见下方「为什么需要它」。

## 为什么需要它（2026-09-18 实证）

驱动一本真书（``prj_2567bb8de642``）跑完整个流水线后，诚实的情况是：一章可以带着
``status = COMMITTED`` 停在库里，而「可交付」此前**没有任何单一出处**——
提交门的硬停只有一份很窄的结构性白名单（``quality/issues.py:BLOCKING_RULES``），
其余信号各散一处，没人汇总：

| 观测到的现象 | 当时的表现 |
|---|---|
| 一章 9 段逐字重复（章内重复 20.3%、trigram 30.37%） | 以 ``overall: 90`` 提交成功 |
| 两章低于字数带（1791 / 1875 可见字 vs 目标 2500） | 长度偏差只记一条 warning，无人消费 |
| 某章开篇 300 字内零冲突 | 黄金三章规则只作为 warning 出现，无人消费 |
| 散文级连贯错误（凌晨场景写到天亮、引用了从未发生的事件、流程错误） | 没有任何规则看得见 |

前三条本模块已经接住（第三条经 ``signing_check`` 的 ``ch1_conflict_300`` fail 档）；
第四条**接不住**，见文末「已知盲区」。

## 判定档位（闭合四档，推导而来）

| verdict | 含义 |
|---|---|
| ``deliverable`` | 证据齐备、无降级项——可以交 |
| ``needs_work`` | 有已知待办（confirm 未接受 / 出带 / 签约硬规则 fail / 未提交 / 证据未评估） |
| ``not_deliverable`` | 硬停（block 档 issue，或字数偏离超过带边缘到目标的距离） |
| ``not_a_candidate`` | 还不是交付对象（``PLANNED``，没有正文） |

``not_a_candidate`` **刻意**与 ``not_deliverable`` 分开：后者说「交了会出事」，前者说
「还没有东西可交」。混为一谈会让「27 章未写」看起来像「27 章写坏了」。

## 理由形状

每条理由机器可读：``rule_id``（规则 / 证据来源标识）、``source``（证据出处）、
``severity``（该证据自身的严重度）、``status``（后果档）、``message``（中文说明）、
``evidence``（引文摘录或数字）。

降级规则（哪些 ``status`` 真的把判定往下拉）：

| status | 后果 | 依据 |
|---|---|---|
| ``block`` | 硬停 | ``quality.issues.issue_gate`` == block（阻断白名单） |
| ``length_beyond_band_edge`` | 硬停 | 与 ``chapter_review.basic_checks`` 的 error 级同式 |
| ``confirm`` | 待办 | confirm 档命中且报告内无接受留痕 |
| ``length_out_of_band`` | 待办 | 出带（默认 ±15% 带边缘） |
| ``signing_fail`` | 待办 | 番茄硬规则 fail 档（开篇冲突 / 主角出场 / 金手指） |
| ``not_committed`` | 待办 | 章状态未到 COMMITTED / RELEASED |
| ``not_evaluated`` | 待办 | 证据源不可得（无报告 / 无草稿 / 体检器不可得） |
| ``warn`` | 仅展示 | severity=warning 且 gate=auto（**允许**静默通过） |
| ``accepted`` | 仅展示 | confirm 档已有显式接受留痕 |
| ``out_of_scope`` | 仅展示 | 该证据源按设计不适用于本章 |
| ``not_a_candidate`` | 仅展示 | 章未写正文 |
| ``project_*`` | 不参与章级阶梯 | 项目级汇总陈述（只在 ``project_reasons`` 里） |

**为什么不按 severity 降级**：``gate`` 与 ``severity`` 正交是仓内既有裁决
（``quality/issues.py`` 的 ``Gate``：severity 答「多严重」，gate 答「谁能说通过」）。
``gate=auto`` 的 warning 语义就是「允许静默通过」；若把 warning 一律算作待办，
本项目 111 份历史报告几乎每章都有 warning，``deliverable`` 会变成死档。反过来，
confirm 档存在的理由正是「不许静默通过」，所以未获显式接受的 confirm 必须降级。

**证据缺失 ≠ 通过**：任一证据源不可得（或报告评的草稿版本 ≠ 当前最新正文），
都落一条 ``not_evaluated`` 理由并降级到 ``needs_work``；绝不允许「因为量不出来
所以当作合格」。

## 证据源（全部只读）

| 源 | 取法 | 不可得时的表现 |
|---|---|---|
| 质量报告 | 每章最新一行 ``quality_reports``（``QualityService.latest_report``）；``issues_json`` 的 gate 由 ``issue_gate`` **重推**（不信任落库 ``gate`` 字段值——本项目 111 份历史报告早于该字段，重推后仍正确分档） | ``DELIVERY_QUALITY_REPORT_MISSING`` → not_evaluated |
| 报告与正文的版本一致性 | ``quality_reports.draft_version`` vs 当前最新 ``drafts.version`` | ``DELIVERY_QUALITY_REPORT_STALE`` → not_evaluated |
| 字数带 | 正文走 ``domain.chapter.draft_resolver.resolve_draft``（共享解析单点，`draft_version=None` = 有意取最新）；目标字数走 ``genre.target_words.resolve_target_word_count``；带覆盖走 ``projects.word_band_json`` + ``wordcount.resolve_band_config`` | ``DELIVERY_LENGTH_UNMEASURED`` → not_evaluated |
| 签约体检 | 整项目跑一次 ``signing_check.service.run_signing_check``，把项目级 items 按章号摊到单章 | 1~3 章 ``DELIVERY_SIGNING_CHECK_UNMEASURED`` → not_evaluated；体检器异常 → 证据源 ``reachable=false`` |
| 章状态 | ``chapters.status`` | —— |

响应里的 ``evidence_sources`` 逐源给出 ``reachable`` + ``detail``（诚实面：看不到的
要说出来）。任一只读路径抛异常时**不**把结果伪装成「合格」——受影响章节落
``not_evaluated``，异常文本进 ``detail``。

## 签约体检的适用范围（决策）

``signing_check`` 是**黄金三章 / 签约窗口**工具，本模块按以下口径消费：

- **只对第 1~3 章计入判定**（``SIGNING_SCOPE_MAX_CHAPTER = 3``）：
  ``ch1_conflict_300`` / ``ch1_protagonist_500`` / ``ch2_golden_finger`` /
  ``ch3_climax`` 本就只对这 3 章成立；
- ``ch2_golden_finger`` 是**窗口**规则（前两章的前 1000 字），因此同时摊到第 1、2 章；
- 此范围内 **fail 档降级**为 ``needs_work``（番茄硬规则），**warn 档只展示**
  （「1500~2200 最佳」「章末留钩子」是建议，且与 F-19「可测算子只作体检、不作处方」
  一致），info 档（含 ``genre_opening_*``）不进理由；
- **第 4 章及以后标记 ``out_of_scope``**（显式理由，不是静默跳过）——这些章的交付
  门槛由质量报告与项目字数带承担，不是「番茄建议」；
- **项目级项不摊到单章**：``echo_words`` / ``signing_window`` / ``empty`` 无章号归属。

## Python API

```python
from packages.core.delivery import (
    build_delivery_report,    # 项目级报告（JSON-ready dict）
    build_chapter_verdict,    # 单章判定行
    collect_project_evidence, # 证据取数（只读）
    derive_chapter_verdict,   # 纯推导：证据 → (verdict, reasons)
    project_verdict_of,       # 纯推导：各章判定 → 项目档
)

report = build_delivery_report(db_path, "prj_xxx")
report["project_verdict"]          # deliverable / needs_work / not_deliverable / not_a_candidate
report["roll_up"]["needs_work_chapter_ids"]
report["chapters"][0]["blocking_reason"]
```

模块布局（每层都能单独测）：

```
verdict.py    纯推导层（零 IO）：Reason / 各证据数据类 / derive_chapter_verdict /
              project_verdict_of / classify_length_tier（两级阈值同 review 侧）
evidence.py   证据取数层（只读 DB）：collect_project_evidence / signing_items_by_chapter
models.py     pydantic 响应模型（供 FastAPI response_model 生成 OpenAPI / 前端类型）
service.py    服务层：证据 → 模型 → JSON-ready dict
```

## HTTP

```
GET /api/projects/{project_id}/delivery-verdict
```

- 200 → ``DeliveryVerdictResponse``：

```jsonc
{
  "project_id": "prj_2567bb8de642", "project_name": "快穿：收账人",
  "generated_at": "2026-09-18T11:20:00+00:00",
  "project_verdict": "needs_work",
  "project_reasons": [{"rule_id": "DELIVERY_PROJECT_NEEDS_WORK", ...}],
  "roll_up": {
    "chapter_count": 30, "written_chapter_count": 3,
    "deliverable_count": 0, "needs_work_count": 3,
    "not_deliverable_count": 0, "not_a_candidate_count": 27,
    "needs_work_chapter_ids": ["ch_92bac068ff0d", "..."],
    "blocking_reason_by_chapter": {"ch_92bac068ff0d": "trigram 重复率 11.00% > 8%（confirm 档：须显式接受才放行…）"}
  },
  "evidence_sources": [{"key": "quality_reports", "reachable": true, "detail": "..."}],
  "signing_check_scope": "签约体检（黄金三章 / 签约窗口口径）只对第 1~3 章计入交付判定…",
  "chapters": [
    {"chapter_id": "ch_92bac068ff0d", "number": 1, "status": "COMMITTED",
     "verdict": "needs_work", "blocking_reason": "...",
     "length": {"visible_chars": 2248, "target_word_count": 2500, "band_low": 2125,
                "band_high": 2875, "deviation_pct": -10.1, "status": "in_band", "tier": "in_band"},
     "quality": {"report_id": "qr_4a32da37cb4c", "overall": 90, "draft_version": 13,
                 "gate_summary": null, "accepted_override": null, "issues": [...]},
     "signing": {"scope": "in_scope", "items": [...]},
     "reasons": [{"rule_id": "RULE_STYLE_REPETITION_TRIGRAM", "source": "quality_issue",
                  "severity": "warning", "status": "confirm", "message": "...",
                  "evidence": "trigram 重复率 30.37% > 确认阈值 25%"}]}
  ]
}
```

- 404 → ``{"detail": "project '...' not found"}``
- 500 → ``{"detail": "delivery verdict failed"}``

router 由 ``discover_routers()`` 自动发现（``packages/core/api/routers/delivery.py``），
``main.py`` 零改动。schema 漂移后按仓内流程 regen：
``python scripts/export_openapi.py && python scripts/gen_frontend_types.py``。

## 实测（2026-09-18，``prj_2567bb8de642``，只读副本）

> **口径变更注记（2026-09-18 晚）**：trigram 重复率改为两档（warn 0.16 / confirm 0.25，
> 见 ``packages/core/quality/README.md`` §4.3）。下方记录里的 trigram 条目当时按旧口径
> （> 0.08 即 confirm）判为 ``confirm``；**按新口径复算**（只读重跑）：第 1 章 11.00% ⇒
> ``auto``、第 2 章 17.07% ⇒ ``warn``、第 3 章 15.05% ⇒ ``auto``——三章都**不再**要求签字，
> 章档由 ``ch1_conflict_300`` 等签名项决定。

- 项目档 ``needs_work``：3 章已写全带待办，27 章未写；
- 第 1 章 ``needs_work``：``RULE_STYLE_REPETITION_TRIGRAM``（11.00%，旧口径 confirm 档、
  报告内无接受留痕）+ ``ch1_conflict_300`` fail + ``ch2_golden_finger`` fail；
  字数 2248 在带内（2125~2875）；
- 第 2、3 章同理（trigram 17.07% / 15.05%，旧口径 confirm 档未接受）；
- 第 4~30 章 ``not_a_candidate``（``PLANNED``）。

## 测试

| 文件 | 覆盖 |
|---|---|
| `tests/unit/test_delivery_verdict.py` | 纯推导层逐分支：干净章 / 出带 / 超带边缘 / confirm（含已接受）/ block / 无报告 / 无字数 / 全空 / 报告过期 / 未提交 / 签约 fail / 签约 warn / out_of_scope / 体检不可得 / PLANNED / 项目汇总 |
| `tests/api/test_delivery_api.py` | 端点形状与档位：rows + roll-up、gate 重推、接受留痕解封、缺失证据落 not_evaluated、报告过期、出带两档、PLANNED、out_of_scope、404 |

## 边界与已知盲区

- **只读聚合，不产生新证据**：本模块不重新跑质量评估、不跑 LLM judge；它把既有信号
  汇总成判定，因此判定的上限 = 既有算子的上限。
- **散文级连贯错误看不见**：凌晨场景写到天亮、引用了从未发生的事件、流程错误这类
  语义问题，机器规则（含质量报告的 timeline/continuity 子分）目前看不见——
  本模块**不会**把它们伪装成已检查（它们既不在 reasons 里，也不在「已评估」的证据
  源里；``evidence_sources`` 只声明本模块真正读过的四个源）。
- **不做全书弧级判断**：只判「单章 + 项目汇总档 = 各章最坏值」。跨章节奏衰减、
  弧末结算是否到位属 ``arc`` / 结构面，不在本模块。
- **签署窗口与经济性**（2万/5万/8万节点）仍由 ``signing_check`` 的项目级
  ``signing_window`` 提示承担，本模块只消费单章项，不重复实现。
- **不阻断任何流程**：本模块只回答「能不能交」，不参与 commit / export 的任何分支
  （未来若要接门禁，应先由用户裁决；当前是报告面）。
