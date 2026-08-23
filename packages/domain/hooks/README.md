# domain.hooks（伏笔与叙事债务）

> 职责：伏笔（`hooks`）+ 叙事债务（`narrative_debts` 表）+ 揭示策略（`reveal_policies`）的 CRUD 与一致性，对应 PRD §22；为 Hook Ledger / Narrative Debt 面板（Sprint 9）提供事实来源。
> 状态：空骨架（规划 Sprint 1 服务层，Sprint 9 UI 深化）。

## 职责与边界
做：伏笔 / 债务登记、`expected_payoff` 校准、揭示策略 CRUD、与 `chapter_outlines` 的绑定。
不做：UI 视图（属 Sprint 9）；自动推荐 expected_payoff（属 Sprint 11 参照系）。

## 对外接口（规划中）
- `Service.add_hook / add_debt / add_reveal_policy`
- `Service.list_open / mark_paid / expected_payoff_calibration`

## 依赖
- 上游：`packages/core/db.py`、`packages/domain/plot/`（大纲绑定）、`packages/core/evaluation/`（Sprint 11 校准）

## 使用 / 入口
待实现（Sprint 1 服务层，Sprint 9 UI 深化）。

## 维护注意点
- `expected_payoff` 校准必须基于黄金三章机检（Sprint 11）；不允许 LLM 直觉赋值。
- 揭示策略 `reveal_policies`（v1.1 新增表）需与角色 `visibility` 联动校验。
- 权威文档：`database/migrations/0001_init.sql`、PRD §22；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 9/11。