# 会话交接（2026-10-23 晚）· 用户报 bug 修复批 + 对抗审查 B 第三轮 + **3.10.1 发版（中途停）**

> **✅ 2026-10-23 23:5x 补记（后续会话执行）**：本文 §6 的 2、3 两项**已完成** —— 轻量 tag **`v3.10.1` 已打在 `50a556e`** 并已推远端；**master 已推至 `e94e742`**（含批次三十三之一）。本文其余内容保持 20:56 的原始快照口径（不改历史），**发版动作以本补记为准**。仍未闭合的只有「档③ 全量 476 条突变未跑」（按当晚裁决已降级为非阻塞）。

> **用途**：新会话接手用的单页事实表。凡本文写的数字均来自本会话**实测**；未测的直接标「未验证」。
> **本次是「用户要重启电脑」而写的停机交接** ⇒ 请从 §2 的「重启后第一件事」开始。
> 相关留痕：`CHANGELOG.md` 的 `## [3.10.1] - 2026-10-23` 段、`AGENTS.md`（硬规则 11 扩写 + 坑区 3 条）、
> `docs/impl/reviews/remaining-debt-2026-10-23.md` 的「四之五 · B5」小节、`scripts/README.md`。

---

## 1. 仓库状态（交接时实测，2026-10-23 20:56）

| 项 | 值 |
|---|---|
| HEAD | **含本交接文件的那次提交**（`chore(release): 归档 3.10.1`）——**本文件不自指 SHA**（自指无法自洽），`git log --oneline -3` 即可查 |
| 分支 | `master`，**领先 `origin/master`（`fbc859f`）22 个提交** ⇒ **全部未推送** |
| 工作树 | **干净**（`git status --porcelain` 0 行）——本轮所有改动（4 个修复 commit + 3.10.1 归档 + 本交接文件）**均已提交**，但**一个都没推** |
| 树指纹 | **767 文件 / `15b014e0d870f1add6c3b91d182839aa1be07ff99f77b81923f02e5fe5a71ee0`**（⚠️ 与闸门前的 `767 ceb3dc81f0eeaa…` **不同**，原因是 `packages/core/api/routers/model_configs.py` 的 **mtime** 变了（内容逐字节未变，见 §5）⇒ **指纹口径含 mtime**，故 mtime 变即指纹变） |
| 版本六处 | pyproject / package.json / package-lock.json / uv.lock / plugin.json / `skills/novelos/SKILL.md` 的 `novelosVersion` ⇒ **六处全部 3.10.1**（已实测逐位一致） |
| 全量 pytest（`-n 20`） | **4359 passed / 1 skipped / 1 xfailed / 0 failed**（163.09s，basetemp 指仓外） |
| ruff | `packages scripts tests` ⇒ **All checks passed!** |
| 突变 | 档① 预检 **84 规格 / 476 条 / 0 问题**；档② 增量 **0 条**；**档③ 全量 476 条串行 ⇒ 本次手动中止（跑约 169 条，35%）**，见 §5 |
| 突变残留 | `.mutbak*` **0**（并清掉一个 **2026-10-15 的历史残留** `packages/core/api/routers/model_configs.py.mutbak`）；生产源码 `MUTATION-` 标记 **0** |
| tag | 本地 **23** 个，最新 `v3.10.0` ⇒ **本次的 `v3.10.1` 尚未打**（命名惯例 = **`v` 前缀**，历史 tag 一律轻量） |
| 服务 | **18081 被 pid 43772 持有**（分离进程，父 pid 10484）⇒ **电脑重启后不会自动拉起**，见 §2 |

## 2. 重启后第一件事

1. **拉起服务**（分离进程，避免被后台任务时限杀掉）：

   ```bash
   cd /d/zcodeproject/NovelOS && .venv/Scripts/python.exe "C:/Users/aistation/AppData/Local/Temp/novelos_serve_detached.py"
   # 验证：curl -s http://127.0.0.1:18081/api/health   ⇒ {"status":"ok","version":"3.10.1","tables":42}
   ```

   ⚠️ 该脚本在 `C:\Users\aistation\AppData\Local\Temp\` 下 ⇒ **Temp 被清则脚本消失**，需按 §9 的内容重建（很短）。

2. **不必重建前端 dist**：本批 `apps/web/dist` 已用新代码构建过（`index-BgkboWF8.js` / `index-DYERPtCB.css`），`dist` 被 gitignore ⇒ **重启后仍在**。

3. **建议先做 §6 的「发布决策」**（提交 + tag + 推）—— 这三步是纯 git 操作，不依赖服务。

## 3. 本会话做了什么（4 个 commit，已提交、**未推**）

| commit | 内容 |
|---|---|
| `c5ac7b7` | fix(前端)：**修复用户报的「删除旧模型档案报 409」**——候选链可见可移除 + 409 提示可读（新增 `AiSettingsCapabilityBindings.tsx` 388 行 / `AiSettingsBindingMeta.ts` 89 行；`AiSettingsPage.tsx` 1648 → 1463 行；测试 +5 例 T1~T5） |
| `f17191e` | fix(对抗审查 B 三轮 · major#1 + minor#2/#3/#6)：**凭据守卫归属闸门可被别名型键伪造**（拆 `entity_keys` / `known_keys`；`--force-all-dangling` 语义统一为「归属确认」；`migrate_secrets` 独立归属闸门；采集口径对齐生产。结构拆分：`check_content_repo_guard.py` 1409 → 644 + 新 `_guard_secrets.py` 1040） |
| `15f096c` | fix(对抗审查 B 三轮 · minor#4)：`run_spec.py` 并发档位 exit 3 存在早退旁路 ⇒ 抽 `_tier_lines()` / `_tier_exit()` 统一出口（守卫 12 → 19 例） |
| `dc05f57` | docs：文档四件套同步 + **性能带结论行订正**（改为 2.3~3.2× / ≤5%，旧值「15%~87%」标注作废并改为「跨上下文不可比」） |

> 前序还有 17 个 commit 未推（批次二十四~二十九 + 第二/三轮审查相关），本会话未动它们。

## 4. 本文件所在提交里的 8 个归档改动 = **3.10.1 发版归档**（**已提交、未推**）

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | `[project].version` 3.10.0 → **3.10.1**（单一源） |
| `apps/web/package.json` | `version` → **3.10.1** |
| `apps/web/package-lock.json` | 顶层 `version` + `packages[""].version` → **3.10.1**（⚠️ 文件里第三处 `3.10.0` 是 `std-env` 这个 npm 包自己的版本，**不要动**） |
| `uv.lock` | `novelos` 段 version → **3.10.1**（`uv sync` 自动改） |
| `.claude-plugin/plugin.json` | `metadata.novelosVersion` → **3.10.1** |
| `skills/novelos/SKILL.md` | `metadata.novelosVersion` → **3.10.1** |
| `CHANGELOG.md` | `## [Unreleased]` → **`## [3.10.1] - 2026-10-23`**，并加回空的 `## [Unreleased]` |
| `apps/web/openapi.json` | schema 漂移后重新导出（version 进 schema）⇒ **必须与代码同步提交** |

⚠️ **`uv sync --extra dev` 已跑过**（editable 元数据已刷成 3.10.1，服务 `/api/health` 已报 3.10.1）。
⚠️ `apps/web/src/api/types.generated.ts` **本次无变化**（version 不进前端类型）⇒ 不在改动清单里是正常的。

### 本会话的一次真实失误（已修，留痕）

第一次归档只改了 `pyproject.toml` + `package.json` 就跑全量 pytest ⇒ **11 个测试红了**（漏 4 处版本副本 + `openapi.json` 漂移）。
**是那 3 个版本一致性守卫测试 + OpenAPI 契约测试当场抓住的** —— 这正是「纪律写在文件里 ≠ 纪律被执行」的实证。
补齐后全量 pytest **4359 passed / 0 failed**。

## 5. 突变闸门中止的如实记录（**本次唯一的未完成闸门**）

**经过**：按发版纪律跑了档③（全量 476 条，串行）作为发版前闸门，用分离进程启动（`novelos_mut_gate_detached.py`，pid 24368），
日志 `C:\Users\aistation\AppData\Local\Temp\novelos_mut_3101.log`。
**用户说「停止跑闸门，太久了」⇒ 手动 kill**（pid 24368 /T）。

| 项 | 值 |
|---|---|
| 中止时进度 | 跑到第 **约 40/84** 个规格文件（`g5_model_config.py`），**CAUGHT 169 / SURVIVED 0 / ABORT 0 / INVALID 0**，用时 **1360s（22.7 min）** |
| 覆盖度 | 476 条里约 **169 条（35%）** 已跑且全部被抓住 ⇒ **剩余约 307 条未跑** |
| ⚠️ 结论 | **不得声称「476/476 已验」**。上一批（改动前）曾 476/476 全 CAUGHT；**本批改动后的全量未验** |
| ⚠️ 已知风险 | 本批**确实改动了突变靶子**：`scripts/check_content_repo_guard.py` 大改 + 新增 `scripts/_guard_secrets.py` + `tests/mutation_specs/run_spec.py` 本身被改 ⇒ 旧规格的锚点可能指向已被搬走/改写的逻辑 ⇒ **档① 预检 0 问题只证明锚点仍唯一命中，不证明突变仍测得到目标逻辑** |

**中止后的残留处置（已做完，工作树已恢复）**：

1. kill 时进程正停在 `packages/core/api/routers/model_configs.py` 的**突变态**（删掉了 `response_model=ConfigTestResult,` 一行，sha `22fb5247…`）。
2. ⚠️ **踩到一个真实陷阱**：用 `git checkout --` 还原 ⇒ 本仓 `core.autocrlf=true` **且无 `.gitattributes`** ⇒ git 把该文件写成 **CRLF**（263 处）⇒ sha 从 `64c2433f…` 变成 `f6dea588…` ⇒ **字节变了、指纹变了**。
   **正解**：写回 `git show HEAD:<path>` 的 blob 字节（LF）⇒ sha 回到 `64c2433ffa1d1624…`（**与突变日志记录的「还原校验 OK」值逐位一致**）⇒ 内容确认恢复。
3. `git add` 该文件只为**刷新 index 的 stat**（`git diff --cached` 为空 ⇒ blob 未变）⇒ 消除 autocrlf 造成的**假 M**。
4. 清掉 `packages/core/api/routers/model_configs.py.mutbak`（**mtime 是 2026-10-15 ⇒ 历史残留，不是本次产生**）⇒ 现在 `.mutbak*` = 0。
5. 现状：`git status` 只剩 §4 的 8 个文件；生产源码 `MUTATION-` 标记 0。

> **⇒ 给下个会话的硬提醒**：本仓**还原突变态不要用 `git checkout --`**（会翻行尾）；要用**执行器自己的 `.mutbak` 备份**，或写回 `git show HEAD:<path>` 的 blob 字节，并**用 sha256 对账**。

## 6. 未完成的动作（按序做即可，全是 git 操作）

1. ~~**提交 §4 的 8 个文件**~~ ⇒ **✅ 已完成**（随本交接文件一起入库，`chore(release): 归档 3.10.1`）。若后续又改了这 8 个文件之一，重新提交即可。

2. **打轻量 tag**（惯例 = **`v` 前缀**、轻量）⇒ **⬜ 待做**：

   ```bash
   git tag v3.10.1          # 打在「版本号已对齐 + CHANGELOG 版本段已写好」之后那个 commit 上
   ```

3. **推送**（用户**已授权**本会话推 master + 推 tag，但**本次未执行**）：

   ```bash
   git -c http.curloptResolve="github.com:443:140.82.112.3" fetch origin   # 先 fetch 核对远端
   git -c http.curloptResolve="github.com:443:140.82.112.3" push origin master
   git -c http.curloptResolve="github.com:443:140.82.112.3" push origin v3.10.1
   ```

   ⚠️ **push 前必须先 fetch**（不 fetch 看不到远端是否前进）。
   ⚠️ 本机 DNS 把 `github.com` 解析到**不通**的亚太边缘 IP ⇒ 需 `curloptResolve` 固定到美/欧边缘（如 `140.82.112.3`）；
   若已开 VPN 使直连可用，则**先测直连再决定要不要绕**（`curl -sS -m 12 -o <仓库内临时文件> -w '%{http_code}' https://github.com/`）。
   ⚠️ 本机安全策略**禁止 `env -u` 包装** ⇒ 用普通 `unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY`。

4. **（可选，建议）补跑档③ 全量突变**（476 条串行 ≈ 50 分钟）⇒ 跑完才能说「发版前闸门已过」。
   跑法：`.venv/Scripts/python.exe tests/mutation_specs/run_spec.py --all`（**串行，不传 `--jobs`；并发档位退出码恒为 3，结论不可信**）。

## 7. 活库被改动过（**不可逆** ⇒ 记在这里）

用户报 bug 的诉求是「把旧模型档案删掉」，本会话**在代码上线后**才执行了对活库 `data/novelos.db` 的写操作：

| 步骤 | 结果 |
|---|---|
| 备份 | `C:\Users\aistation\AppData\Local\Temp\novelos_backup_20261023\`（`novelos.db` 一致性备份 128,532,480 B + `secrets.json`） |
| 解绑 | `PUT /api/capability-bindings/creative_writing` → `profile_ids=["mprof_60d0c5bd7949"]`（去掉第 2 位旧档） |
| 删档案 | `DELETE /api/model-profiles/mprof_ab51c2afedfa` → **204**（=「MiniMax-写作」/ MiniMax-M2.7） |
| 清密钥 | `scripts/check_content_repo_guard.py secrets --db data/novelos.db --prune-dangling --apply` ⇒ 清掉随之悬空的 `mprof_ab51c2afedfa` 键，**自动留备份 `secrets.json.bak-20261023_202252`**（仓库根，被 gitignore） |
| 现状 | 档案只剩 **MiniMax**（M3.1-Flash-Preview，被 4 个环节用）+ **K3**（k3-256k，被 8 个环节用）；`mprof_ab51c2afedfa` 在库内残留 **0**、binding 引用 **0**；守卫复检 **exit 0 合规** |

⚠️ **只动了模型档案，零书籍正文/人物/设定改动**，内容仓（`NovelOS-Content` / `NovelOS-Artifacts`）**零接触**。
⚠️ 但这**偏离了「活库只读」的默认纪律**（用户的诉求就是删它）⇒ 已如实登记。

## 8. 用户报的那个 bug：根因与验收（供下个会话判断是否复发）

**三层根因**：
1. `capability_bindings.profile_ids` 是 JSON 数组、**顺序即 fallback 序**；`ModelRouter._candidates()`（`packages/core/model_router/router.py:154-215`）按链顺序解析 ⇒ 后备位**运行期真会被用到**（`call_with_fallback`）。
2. 后端 `DELETE` 在被**任何位置**引用时 409 且**不做静默解绑**（V3.7 既定设计）⇒ **409 正确，不是误报**。
3. **真缺陷在 UI**：环节分配面板**只渲染 `profile_ids[0]`** ⇒ 后备链不可见；档案卡「设为默认」是 **prepend** ⇒ 旧档被顶到第 2 位；唯一改选路径 `bind(cap,[id])` 是**整链替换**且选中原值不触发 onChange ⇒ **全站没有入口能摘掉后备位** ⇒ UI 把可解死结做成不可解。
   附带：409 的 detail 是**对象**，被 `apps/web/src/api/client.ts:105` 的 `JSON.stringify` 甩成**原始 JSON 串**。

**验收（浏览器实测，非仅单测）**：真实点了一次「删除 MiniMax」（仍被 4 环节引用）⇒ 后端正确 409、档案毫发无损，页面提示：

> `mprof_bae369541c72：失败0ms · HTTP 409 · profile '...' 被 4 个 binding 引用（请在「环节分配」中把该档案从以下环节移除：卷纲、推理规划、状态提取、轻量评审）`

⇒ 环节是**中文名**、不再是原始 JSON 串、不再出现那句指向不存在操作的旧文案。
**以后加新模型想换掉旧的**：在「环节分配」里先点候选链成员上的「移除」（链空自动走 unbind），再删档案。

## 9. 环境事实（重启后可能失效，重建时按此）

- **服务启动脚本**：`C:/Users/aistation/AppData/Local/Temp/novelos_serve_detached.py`
  —— 内容要点：`subprocess.Popen([PY, "-m", "uvicorn", "packages.core.api.main:app", "--host", "127.0.0.1", "--port", "18081"], cwd=REPO, creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)`，日志写 `C:\Users\aistation\AppData\Local\Temp\novelos_serve.log`，pid 写 `novelos_serve.pid`。
  **必须分离进程**：本地后台任务有 1 小时 maxRunMs ⇒ 到点被杀 ⇒ 会中断用户正在用的服务。
- **突变闸门分离脚本**：`C:/Users/aistation/AppData/Local/Temp/novelos_mut_gate_detached.py`（同法；执行器被中途杀掉会**留下突变态**）。
- **pytest 必须显式 `--basetemp=<仓外唯一目录>`**（本机 `%TEMP%\pytest-of-AiStation` 属主拒绝访问；指仓内会让「仓外探针」类用例假红）。
- **Windows 判进程存活用 `OpenProcess`**（`os.kill(pid, 0)` 在 win32 语义不稳定）。
- **`taskkill` 要写单斜杠**：`MSYS_NO_PATHCONV=1 taskkill /PID <pid> /F`（写 `//PID` 会被 taskkill 当无效选项）。
- 本机只有 **Python 3.12.15** ⇒ **CI 的 3.11 腿无法本地验证**（且 CI 依赖同级 `D:/zcodeproject/NovelOS-Artifacts` ⇒ CI 上会红，pre-existing）。

## 10. 未验证 / 已知边界（如实登记，不得声称已验）

| 项 | 状态 |
|---|---|
| **本批改动后的全量 476 条突变** | ❌ **未跑完**（约 169/476，35%）⇒ 发版前闸门**未过** |
| CI 的 Python 3.11 腿 | ❌ 本机无法验证；CI 另有 pre-existing 失败（依赖同级 Artifacts 仓） |
| 版本归档后的**前端 vitest** | ❌ **未重跑**（版本号改动不触达前端逻辑，但未实测；上一轮 62 文件 / 853 例绿是版本改动**前**） |
| 推送与 tag | ❌ **未执行**（用户已授权，等 §6 执行） |
| 152 个 ACL 锁死的 Temp 目录 | ❌ 需管理员 `takeown` + `icacls`（151 个 `novelos_openapi_*` + 1 个 `novelos_probe_r2_*`；低危害、无凭据、不再增长） |
| 仓库无 `.gitattributes` + `scripts/` 行尾混用 | ❌ **本批已暴露其真实危害**（`git checkout --` 翻行尾 ⇒ 指纹变），**排期到下一批**（建议加 `.gitattributes` 统一口径） |

## 11. 台账里仍开着的项（本次未派工）

见 `docs/impl/reviews/remaining-debt-2026-10-23.md`：**B-2**（external 加 `source_url` + 离线形态校验 + 纠正 1 条错标）、
**B-1**（separation 精确比交叉核对）、**C-6**（smoke 补 fast_mode / auto_commit / volumes / revise）、
**A-1 / C-1**（突变覆盖排期切片 + 零覆盖清单订正为 22 个目录）。
另：成员 C 上报的性能脚本「父路径无搜索权限（EACCES）」仍会抛栈、成功路径临时库 `<stem>_ch<N>.db` 测完不删。

---

## 12. 常用命令形状（已验证可用）

```bash
cd /d/zcodeproject/NovelOS && export PYTHONIOENCODING=utf-8
.venv/Scripts/python.exe scripts/tree_fingerprint.py                # "767 <sha256>"
.venv/Scripts/python.exe -m ruff check packages scripts tests        # All checks passed!
.venv/Scripts/python.exe -m pytest -n 20 -q --basetemp=C:/Users/aistation/AppData/Local/Temp/<唯一目录>
.venv/Scripts/python.exe tests/mutation_specs/validate_specs.py      # 84 规格 / 476 条 / 0 问题
.venv/Scripts/python.exe tests/mutation_specs/run_spec.py --all --changed --precheck   # 只读预检
.venv/Scripts/python.exe scripts/check_content_repo_guard.py secrets --db data/novelos.db   # 只读巡检
cd apps/web && npx tsc -b && npx vitest run && npm run build
python scripts/export_openapi.py && python scripts/gen_frontend_types.py   # schema 漂移后 regen（用项目 .venv 解释器）
```
