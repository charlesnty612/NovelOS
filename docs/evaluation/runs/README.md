# docs/evaluation/runs/

每次 Regression Eval（`scripts/eval_regression.py`）的运行报告落盘于此，
文件名为 `<run_id>.json`（如 `run_20260823T235500123456.json`），内容含基线引用、
各 case 结果、drift 明细与 decision（PASS / BLOCK）。

> 三仓迁移说明（2026-09-08）：历史运行报告 JSON 已迁至痕迹仓 NovelOS-Artifacts:docs/evaluation/runs/（本目录现仅保留 `.gitkeep` 占位）；后续重建基线后新报告仍按原路径落回软件仓。

- `last_passing_run.json` 不在此目录，位于 `docs/evaluation/baseline/`（历史基线已迁 NovelOS-Artifacts:docs/evaluation/baseline/，软件仓 baseline 目录已清空）。
- 运行报告是 git 跟踪的审计产物；`.gitkeep` 保证目录在空仓库中仍存在。
- 报告文件与基线文件均为机器生成：不要手工编辑，更新请走 `eval_regression.py`。
