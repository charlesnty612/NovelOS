# docs/evaluation/runs/

每次 Regression Eval（`scripts/eval_regression.py`）的运行报告落盘于此，
文件名为 `<run_id>.json`（如 `run_20260823T235500123456.json`），内容含基线引用、
各 case 结果、drift 明细与 decision（PASS / BLOCK）。

- `last_passing_run.json` 不在此目录，位于 `docs/evaluation/baseline/`。
- 运行报告是 git 跟踪的审计产物；`.gitkeep` 保证目录在空仓库中仍存在。
- 报告文件与基线文件均为机器生成：不要手工编辑，更新请走 `eval_regression.py`。
