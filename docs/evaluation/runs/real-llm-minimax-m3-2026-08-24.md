# 真实 MiniMax-M3 端到端验证

```json
{
  "project_id": "prj_9ab8f9668b86",
  "chapter_id": "ch_0f9a1e5f5952",
  "status": "COMMITTED",
  "draft_prefix": "天还没亮透，少年的背包已收拾妥当。地质锤、放大镜、采样袋、罗盘、卷尺、笔记本，他逐件清点了一遍，最后在清单末尾画了个勾。笔记本封皮上写着\"北岭外谷·初勘\"五个字，字迹小而工整。背包侧袋里，他又塞进一卷",
  "think_free": true,
  "state_keys": [
    "characters",
    "debts",
    "events",
    "hooks",
    "recent_events",
    "state_version",
    "world"
  ],
  "quality": {
    "report_id": "qr_341e70db7af8",
    "project_id": "prj_9ab8f9668b86",
    "chapter_id": "ch_0f9a1e5f5952",
    "commit_id": null,
    "run_id": "wfr_cb6301eb956d",
    "overall": 87,
    "scores_json": {
      "overall": 87,
      "plot": 100,
      "character": 100,
      "continuity": 100,
      "style": 95,
      "pacing": 85,
      "foreshadowing": 0,
      "_meta": {
        "scoring_version": "quality-scoring-v0",
        "llm_judge": "deferred",
        "evaluated_at": "2026-08-23T17:16:20.065714+00:00",
        "scoring_formula_hash": "ae713bdfb922097f"
      }
    },
    "issues_json": [
      {
        "severity": "info",
        "category": "compliance",
        "location": "<unknown>",
        "rule_id": "Q6_NO_REFERENCES",
        "message": "未配置参照书，跳过 Q6 检查",
        "suggestion": null,
        "evidence_refs": null,
        "judge_trace": null
      },
      {
        "severity": "info",
        "category": "compliance",
        "location": "<unknown>",
        "rule_id": "RULE_Q8_NO_DATA",
        "message": "未记录 ai_chars / human_chars",
        "suggestion": null,
        "evidence_refs": null,
        "judge_trace": null
      },
      {
        "severity": "warning",
        "category": "payoff",
        "location": "<unknown>",
        "rule_id": "RULE_H1_NO_END_HOOK",
        "message": "末 200 字未命中任何钩子标记",
        "suggestion": null,
        "evidence_refs": null,
        "judge_trace": null
      },
      {
        "severity": "warning",
        "category": "payoff",
        "location": "<unknown>",
        "rule_id": "RULE_H4_NO_EARLY_CONFLICT",
        "message": "前 300 字未命中任何冲突词（H-4 黄金三章）",
        "suggestion": null,
        "evidence_refs": null,
        "judge_trace": null
      },
      {
        "severity": "warning",
        "category": "payoff",
        "location": "<unknown>",
        "rule_id": "RULE_H4_NO_HOOK",
        "message": "前 3 章末段无钩子（H-4）",
        "suggestion": null,
        "evidence_refs": null,
        "judge_trace": null
      }
    ],
    "created_at": "2026-08-23T17:16:20.065714+00:00"
  },
  "timings": {
    "plan": 47.125,
    "write": 104.90600000001723,
    "review": 0.0629999999946449,
    "commit": 382.68799999999464
  },
  "ai_call_logs": [
    {
      "model_id": "openai_compatible/MiniMax-M3",
      "latency_ms": 104750
    },
    {
      "model_id": "openai_compatible/MiniMax-M3",
      "latency_ms": 206250
    },
    {
      "model_id": "openai_compatible/MiniMax-M3",
      "latency_ms": 176187
    },
    {
      "model_id": "openai_compatible/MiniMax-M3",
      "latency_ms": 47000
    }
  ]
}
```
