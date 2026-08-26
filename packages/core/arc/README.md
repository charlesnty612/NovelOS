# packages/core/arc — 叙事弧光聚合视图

V3.2 P2-2 引入的「只读弧光视图」模块。目标：把分散在
chapters / quality_reports / drafts / hooks / narrative_debts 五张表里的节奏数据，
按一屏 dict 汇总输出，让作者 / 前端能在一处判断「弧线是否健康」。

## 对外接口

```python
from packages.core.arc import build_arc_view

result = build_arc_view(db_path, project_id)  # -> dict
```

主入口 `build_arc_view(db_path, project_id) -> dict` 是纯函数；只在内部连接
DB 拉数，对外不持状态。`project_id` 不存在时抛 `ValueError`，由 router 转 404。

## 返回结构（口径）

```jsonc
{
  "project_id": "prj_xxx",
  "generated_at": "2026-08-26T...+00:00",
  "chapters": [
    {
      "number": 1,
      "title": "第 1 章",
      "has_payoff_beat": false,
      "charge_beat": false,
      "overall": 81,        // 最新 quality_report.overall；无则 null
      "pacing": 65,         // 最新 quality_report.scores_json.pacing；无则 null
      "prose_chars": 1700   // 最新 draft.content 去空白字符数；无则 null
    },
    ...
  ],
  "payoff": {
    "total": 9,                       // 全章 payoff 章数
    "charge_streak": 0,                // 尾部连续「蓄力且未兑现」章数
    "max_charge_streak": 1,            // 历史最长蓄力连击
    "last_payoff_chapter": 55          // 最近 payoff 章号；无则 null
  },
  "hooks": { "open": 62, "resolved": 4, "overdue": 0 },
  "debts": { "open": 0, "paid": 0 },
  "alerts": [
    { "level": "warn", "code": "low_payoff_density",
      "message": "最近 5 章 payoff 占比 0%（阈值 40%）" }
  ]
}
```

### `chapters[i].has_payoff_beat` / `charge_beat` 判定口径

按 `chapters.plan_json.key_beats` 解析（容错三态）：

1. `key_beats` 是 `list[dict]`：取每项 `purpose` 字符串扫描 `[payoff]` / `[charge]` 标签；
2. `key_beats` 是 `list[str]`：直接对字符串扫描；
3. `key_beats` 缺失 / 不是 list / 解析失败 / 无标签 → `False, False`。

正则大小写不敏感（`[PAYOFF]` / `[payoff]` 都算）。

### `charge_streak` / `max_charge_streak` 口径

- 「蓄力章」定义：本章 `charge_beat=True` 且 `has_payoff_beat=False`。
- 同章同时含 payoff+charge 视为**兑现章**（不计入蓄力连击，也不打断尾部连击）。
- `charge_streak`：从最后一章倒推的连续蓄力章数（兑现即停）。
- `max_charge_streak`：全章序内蓄力连击的最大值。

### `hooks.overdue` 口径

仅统计 `status ∈ {OPEN, ACTIVE, ESCALATED}` 的伏笔。
若 `introduced_chapter_no`（来自 chapters.number）距当前最大章号 > 阈值，计为 overdue。
阈值优先读 `projects.foreshadow_overdue_chapters`（项目级可配，0008 迁移引入，
Sprint 15 / V1.3），列缺 / NULL / 非正 → fallback 30。
（与 `packages.core.context_engine.builders._open_foreshadow_list` 同语义，但本
模块独立维护 SQL 以避免 import 业务 builder。）

## 告警规则（模块常量）

| code | level | 触发条件 | 阈值常量 |
| --- | --- | --- | --- |
| `charge_streak_exceeded` | fail | `charge_streak >= 3` | `_CHARGE_STREAK_FAIL = 3` |
| `low_payoff_density` | warn | 最近 5 章 payoff 占比 < 40% | `_PAYOFF_DENSITY_WINDOW = 5` / `_PAYOFF_DENSITY_MIN_RATIO = 0.4` |
| `foreshadow_overdue` | warn | `hooks.overdue > 0` | 见上「hooks.overdue 口径」 |
| `low_pacing_chapter` | warn | 任一章 `pacing < 50`（多条合并到 message） | `_PACING_FAIL_THRESHOLD = 50` |

alerts 顺序按规则编号；同一项目可同时触发多条。

## REST 端点

`packages/core/api/routers/arc.py` 暴露：

```
GET /api/projects/{project_id}/arc
```

- 200 → 返回 `build_arc_view` 的 dict；
- 404 → `{"detail": "project '...' not found"}`；
- 500 → `{"detail": "arc view failed"}`。

沿用 `signing_check.py` 的「`db_path` 走 `request.app.state.settings.db_path`」模式；
router 顶层 `router = APIRouter(tags=["arc"])`，由 `discover_routers()` 自动挂载
到 `/api` 前缀。

## 设计要点

- 纯启发式规则、无 LLM、无新依赖；
- DB 取数与判定逻辑**显式分层**：`_load_*` / `_summarize_*` 一组 IO 辅助，
  `_parse_beat_flags` / `_count_charge_streaks` / `_payoff_ratio_recent` /
  `_compute_alerts` 一组纯函数（便于单测覆盖）；
- 不 import 跨包私有函数：所有 SQL 与 fallback 常量在本模块顶部显式声明；
- 容错：plan_json 缺失 / key_beats 形态异常 / 无 quality_report / 无 draft
  一律填空值（`False` / `None` / `0`），不抛异常。

## 测试

- `tests/unit/test_arc.py` —— 纯函数单测（key_beats 三态解析、charge 连击
  边界、alerts 三规则正反例、最近 5 章 payoff 密度）；
- `tests/api/test_arc_api.py` —— REST 集成测试（正常路径、404、空项目
  容错、plan_json 字段缺失容错）。