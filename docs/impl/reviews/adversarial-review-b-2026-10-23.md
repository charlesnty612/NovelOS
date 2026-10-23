# 对抗审查 B —— 2026-10-23（专职对抗审查者，只读）

> 角色：Verifier / 对抗审查 B。目标：**驳倒**声称，不是确认正确。
> 纪律：只读（未改仓库任何文件、未做任何 git 写操作、未跑真实 LLM、未碰内容仓数据）、
> 造数据一律用临时库 / 临时副本、pytest 一律显式 `--basetemp=<系统临时目录唯一子目录>`、
> 未跑突变执行器实跑（等效实测用**临时副本**自己做）。
> 数字全部自己复算；子代理报告与 Lead 裁决一律当**待验证假设**处理。

## 0. 被审文件快照（并发快照纪律）

审查期间多个子代理并发改源码。下表是**本报告结论所依据的版本**；sha256 变动的文件在
「并发注记」里点名。

### 第一批（A/B/C 组）

| sha256(前16) | 行数 | 文件 |
|---|---|---|
| `5b645d8b37a91ab0` | 387 | `packages/domain/character/service.py` |
| `44f5cf2edb6e76a6` | 24 | `packages/domain/character/__init__.py` |
| `987a8aaa457287a2` | 227 | `tests/unit/test_character_delete_reference_guard.py` |
| `864f5154e2adb89a` | 1127 | `tests/unit/test_no_core_to_workflows_dep.py` |
| `5c359e53e88b2fa3` | 69 | `tests/mutation_specs/t7_character_delete_and_layering.py` |
| `d5725ebe7ca3348d` | 184 | `scripts/eval_regression.py` |
| `9acb78d1a18aeb23` | 831 | `scripts/m1_report.py` |
| `01a8e668abeb439b` | 86 | `.gitignore` |
| `a82f6b274cd76558` | 34 | `docs/evaluation/README.md` |
| `1ba43cac311f1ed0` | 436 | `packages/core/genre/consumers.py` |
| `4d989eb9a745da94` → `6e7c11bd83024888` | 391 → 412 | `AGENTS.md`（**Lead 审查期间新增一段**，见并发注记） |

### 第二批（Task 5）

| sha256(前16) | 行数 | 文件 |
|---|---|---|
| `f10d0b99e889eda2` | 161 | `apps/web/src/hooks/usePoll.ts` |
| `673ee44a62976c46` | 316 | `apps/web/src/hooks/usePoll.test.ts` |
| `6557ad5f8cd6c3e5` | 156 | `apps/web/src/hooks/useChapterRunOrchestration.ts` |
| `47fa62c2644f0f1a` | 213 | `apps/web/src/hooks/useChapterRunOrchestration.test.ts` |
| `acdddf0025437505` | 1135 | `apps/web/src/pages/ChapterDetailPage.tsx` |
| `c95c52b70a278df8` | 3689 | `apps/web/src/pages/ChapterDetailPage.test.tsx` |
| `210970b4bb588eff` | 456 | `apps/web/src/pages/chapter/ChapterPipelineHeader.tsx` |
| `879b2d62a11d6b93` | 242 | `apps/web/src/pages/chapter/ChapterPipelineHeader.test.tsx` |
| `90b09e22162141a6` | 174 | `apps/web/src/pages/chapter/ChapterRunPanel.test.tsx` |
| `5882a876b07caaee` | 109 | `apps/web/src/App.test.tsx` |
| `663dbd43c86a652a` | 30 | `apps/web/src/pages/NotFoundPage.test.tsx` |
| `39ca1bb2237b6d4f` | 703 | `apps/web/src/pages/ProjectsListPage.tsx`（只读参照，未被 Task 5 改动） |

**并发注记**：`AGENTS.md` 在本审查期间由 Lead 改动（`4d989eb9a745da94`/391 行 →
`6e7c11bd83024888`/412 行），新增第 7 条「突变规格不再是新测试的强制项」并改了第 2 条。
**D-15 措辞（硬规则 5，第 71 行）逐字未变**（已与本报告采信的快照逐字比对）⇒ 第一批 C 组
结论不受影响。其余被审文件 sha256 在审查首尾两次快照中**逐位一致**。

---

# 1. 结论摘要

| 组 | 一句话结论 | 是否驳倒 |
|---|---|---|
| **A（Task 7 引用完整性 + 分层守卫）** | 删角色的引用守卫**本身是对的**（两端点都查、抛错前零删除、突变三条全红），但**分层看守有两条真实破口**（动态 import 到 domain 完全不设防、domain 的相对 import 逃逸不设防），且 **world 域同款孤儿洞仍然开着**（删 faction 仍留孤儿关系行）。 | **部分驳倒**（核心修法驳不倒；分层守卫与「两域处置一致」的声称驳倒） |
| **B（三仓边界 / 痕迹归位）** | `.gitignore` 9 条规则与痕迹仓「无 remote」**属实**；但 `_artifacts_root()` 的**最后一档回退就是软件仓本身**，且 `eval_regression.py` 的**基线路径仍指向软件仓** ⇒ 两条路径都能把痕迹写回软件仓。 | **驳倒**（D-0/D-1「必须落痕迹仓」在缺痕迹仓的机器上不成立；D-14/D-19 的防回流成立） |
| **C（D-15 措辞 + 内容仓 push 护栏）** | D-15 措辞与 schema **逐字一致**，但**只改了 AGENTS.md 一处**，实际消费方 `packages/core/genre/consumers.py` 的 docstring 仍是绝对口径；push 护栏**两层都真的挡得住**，但**注释里的三步解除命令第 2 步是无效命令**（exit 129）。 | **部分驳倒**（护栏有效性驳不倒；解除步骤与口径覆盖面驳倒） |
| **Task 5（前端快通道双写入口 + 状态机 hook）** | 三态文案、轮询手动重试、PAUSED→detail 重拉、auto_commit 载荷链**都做到了**，三条突变**全部能红**；但章节页「取消勾选」现在写 `fast_mode: null` ⇒ **对 `fast_mode=NULL` 的项目 UI 永远写不出「显式关」**，且 `NOVELOS_FAST_MODE=1` 时「关掉」实际不生效（后端仍跳过审校闸门）⇒ **不安全方向的假承诺**。 | **驳倒**（「双入口终态逐格相同」这条我核过**成立**；驳倒的是「这个终态安全 / 与 Task 5 之前等价」以及「前后端口径不一致只是显示问题」） |

---

# 2. 发现清单（第一批）

## A 组

### A1 · **major** · 分层看守的 `core→domain` 方向边**只守静态 import**，动态 import 完全不设防

- **文件:行**：`tests/unit/test_no_core_to_workflows_dep.py:110-119`（`LAYERING_DIRECTIONS` 第 4 条）、
  `:341-357`（`scan_static_layering_violations`）、`:550-600`（`scan_dynamic_workflow_imports`，
  目标写死 `packages.workflows`）、`:696-716`（字面量兜底只 grep `packages.workflows`）。
- **证据**（合成源码注入扫描入口，不落盘）：
  ```
  === A) DYNAMIC import of packages.domain from core ===
    static core->domain violations : []
    dynamic workflow scanner       : violations=[] unfingerprintable=[]
    relative-escape scanner        : []
    literal 'packages.workflows' grep hits in probe source: []
    => verdict: dynamic core->domain import is NOT caught by any of the 4 scanners
  ```
  合成源码 = `import importlib` + `importlib.import_module("packages.domain.world.service")`，
  注入位置 `packages/core/quality/_probe.py`。
- **判别**：Lead 裁决「**任何新增的 core→domain 依赖都必须让本看守变红**」**只对静态 import 成立**。
  任何人在 `packages/core/**` 写一行 `importlib.import_module("packages.domain.world.service")`
  （或 `exec("from packages.domain...")`）即完成跨层破口，而四个扫描器 + 字面量兜底**全部放行**。
  该方向边号称是「机器看守」，实际只有约一半的形态被守。
- **建议修法**：把动态扫描的目标从「`packages.workflows` 一个前缀」泛化成一张**目标前缀表**
  （`packages.workflows` + `packages.domain` + `apps`），复用现成的 `_fold_str` / `_mentions_*`
  机制；或在 `core` 根加一条独立规则「动态 import 目标不得以 `packages.domain` 开头」。

### A2 · **major** · 相对 import 逃逸扫描器**只作用于 `packages/core`**，`domain → workflows` 可用相对 import 绕过

- **文件:行**：`tests/unit/test_no_core_to_workflows_dep.py:364-390`
  （`scan_escaping_relative_imports(root_rel="packages/core")`）、
  `:620-626`（**全仓唯一调用点**，root_rel 写死 `"packages/core"`）、
  `:295`（静态扫描只认 `node.level == 0` ⇒ 相对 import 对静态判据不可见）。
- **证据**：
  ```
  === B) domain -> workflows via RELATIVE import (escape scanner scope) ===
    static domain->workflows (level>0 skipped?): []
    escape scanner on 'packages/core'  : [('packages/domain/character/service.py', 1, ... level=3 >= 包深度 3 ...')]
    escape scanner on 'packages/domain': [('packages/domain/character/service.py', 1, ... level=3 >= 包深度 3 ...')]
    package_depth of packages/domain/character/service.py = 3
  ```
  `from ...workflows.chapter_write import pipeline` 在 `packages/domain/character/service.py`
  里 level=3 ⇒ 从 `packages.domain.character` 上溯 3 级 ⇒ **真实解析到 `packages.workflows.chapter_write`**。
  逃逸扫描器**能**抓住它，但**没有任何用例以 domain 为根调用它** ⇒ 这条能力是死的。
- **判别**：`LAYERING_DIRECTIONS` 第 2 条「domain 不得依赖业务流程包」对它**最常见的逃逸形态**
  （相对 import）完全为空。`from packages import workflows` 这种「经父包取子包」形态被守住了，
  但 `from ...workflows.chapter_write import X` 没被守住 ⇒ 守卫是**半边看守**（与文件自己
  记载的 2026-07 批次那次「domain 只扫一半」是**同族复发**）。
- **建议修法**：把 `GUARDED_ROOTS`（core + domain）同样喂给 `scan_escaping_relative_imports`；
  或把逃逸扫描并入 `LAYERING_DIRECTIONS` 的统一遍历循环。

### A3 · **major** · world 域留着**同款孤儿洞**：删 faction 不查 `relationships` ⇒ 孤儿行且 `foreign_key_check` 报不出

- **文件:行**：`packages/domain/world/service.py:533-555`（`_delete` 只在 `cfg.table == "locations"`
  时查 `plot_events.location_id`；**factions / world_rules 无任何引用检查**）；
  对照 `packages/domain/character/service.py:344-353`（character 侧只查 relationships 两端点）。
- **依据**：0022 明确端点可以是 faction（`database/migrations/0022_faction_relationship_endpoints.sql:7-8`
  与 `:62-64`「端点可以是 character_id 或 faction_id（wfr_6619a7bfa6fa 修复）」）；
  `packages/core/story_state/applier.py:242-256` 同样确认端点可指向 faction_id。
- **证据**（临时库 + 合成夹具）：
  ```
  === [4] faction endpoint -> delete_faction leaves orphan? ===
    faction id: fac_97f9fdd6823e
    delete_faction SUCCEEDED (no reference check)
    factions left: 0
    orphan rel rows referencing deleted faction: 1
    PRAGMA foreign_key_check -> []
  ```
- **判别**：「与 world 域对齐 / 两个域处置一致」**只对 character 侧成立**。孤儿洞在 world 域
  **仍然开着**，而且 0022 的设计明确说端点可以是 faction ⇒ 这不是边角料，是设计内的可达状态。
  角色侧的守卫修得对，但它**没有把「删实体留孤儿关系行」这个形状关掉**。
- **建议修法**：world 侧 `delete_faction` 补同样的 relationships 两端点检查（`from_character_id`
  与 `to_character_id` 都查）；更好的做法是把「删实体前查 relationships 端点」抽成**共用守卫**，
  两侧共用一份，避免下一次再漏。

### A4 · **major** · 存量孤儿行**没有任何清理 / 检测路径**；本次修复只防新孤儿

- **文件:行**：`packages/domain/character/service.py:329-359`（只加前置检查）；
  `database/migrations/` 全链**无孤儿清理迁移**（唯一 `DELETE FROM relationships` 在
  `0017_unique_constraints.sql:28`，是唯一索引去重，与孤儿无关）；`scripts/` 无孤儿诊断脚本。
- **判别**：修复前 DELETE 端点无引用检查，且**前端可达**（`apps/web/src/pages/bible/CharacterTab.tsx:72`
  → `apps/web/src/api/endpoints.ts:142`）⇒ 存量孤儿是**可达状态**；而 `PRAGMA foreign_key_check`
  对它**报不出**（实测 `foreign_key_check -> []`，`foreign_key_list(relationships)` 只剩
  project_id 一条 FK）⇒ **连发现手段都没有**。
  仓内其实已经认知这个危险：`packages/core/story_state/commits.py:674` 的回滚路径**专门**在清
  「悬挂 relationships 行」——同一个危险，提交回滚路径处理了，删除路径此前没处理，现在处理了
  **未来**的删除，**过去**的删除没人管。
- **建议修法**：① 加一条**只读诊断**（`relationships LEFT JOIN characters` 找端点悬空），
  让「有没有存量孤儿」变成可回答的问题；② 一次性清理迁移（先备份、只删端点悬空的行、
  留下审计）；③ 至少在 release note / 台账里**显式登记「存量孤儿未清」**，不要让「修好了」
  的印象覆盖掉这个事实。

### A5 · **minor** · `packages/domain/character/service.py:387` 的 `__all__` 未含 `ReferencedError`

- **文件:行**：`packages/domain/character/service.py:387`（`__all__ = ["CharacterService"]`）
  vs `packages/domain/character/__init__.py:13,22`（`from .service import CharacterService, ReferencedError`
  + `__all__` 含 `"ReferencedError"`）。
- **判别**：`from packages.domain.character.service import *` **拿不到** `ReferencedError`；
  `from packages.domain.character import ReferencedError` **拿得到** ⇒ 两条导入路径行为不一致
  （仓内其它域的 `__init__.py` 都在包级导出错误类，见 `packages/domain/world/__init__.py:8,20`）。
- **建议修法**：`__all__ = ["CharacterService", "ReferencedError"]`。

### A6 · **minor** · 白名单「登记制」只强制**声明**，不强制**许可**；且它是同文件里唯一没有字面量基线的白名单

- **文件:行**：`tests/unit/test_no_core_to_workflows_dep.py:1045-1060`
  （`test_core_to_domain_allowlist_matches_actual_imports`，与**实际导入集合**相等）、
  `:1063-1079`（`test_core_to_domain_allowance_is_not_coarse`，6 条负向探针）、
  `:648-665`（`test_dynamic_import_allowlists_are_pinned` —— **另两个**白名单有字面量 pin）。
- **证据**：
  ```
  === C) whitelist mutability ===
    actual core->domain (file,module) pairs = 44
    whitelist entries                     = 44
    equal sets? True
    missing=[]   ghost=[]
    'ALLOWED_CORE_TO_DOMAIN_IMPORTS ==' literal-pin present? False
    dynamic allowlists literal-pinned?      True
  ```
  另：44 条白名单**逐条**核实 —— 全部对应仓内**真实存在的 import 语句**（集合相等），
  全部指向**真实存在的模块文件**（44/44 可解析，probe3 段 1），无目录粒度 / 无通配符 / 无幽灵条目。
- **判别**：新增一条 core→domain import 时**必须**同时登记（测试会红 `missing`）⇒ Lead 那条
  「先登记再写」属实。但登记完成后**没有任何机器约束**判断这条新依赖是否合理 ⇒ 机器兑现的是
  「声明义务」，不是「许可」。且白名单内容随树漂移，**审查看不见漂移**（同文件另两个白名单有
  字面量 pin，正好是本仓登记过的「白名单钉条数 ⇒ 可被悄悄换掉」同族形状）。
- **口径更新**：AGENTS.md 新第 7 条（2026-10-23）已把「突变规格」降为非强制项，判别力主担子
  改为「断言打在真实值上」。据新口径，本条现有断言（集合相等 + 6 条负向探针 + 2 条新增依赖拒绝）
  确实打在真实值上 ⇒ **本条从 major 降为 minor**，但「白名单无字面量基线」这一点仍建议补。
- **建议修法**：给 `ALLOWED_CORE_TO_DOMAIN_IMPORTS` 加一条字面量 pin（照抄
  `test_dynamic_import_allowlists_are_pinned` 的写法），让白名单的**内容**也变成可审的基线。

### A7 · **minor** · 首条 domain→domain 依赖已引入，但分层方向表里**没有** domain→domain 这条边

- **文件:行**：`packages/domain/character/service.py:59`
  （`from packages.domain.world.service import ReferencedError as _WorldReferencedError`）。
- **证据**：
  ```
  === D) full import graph of packages/ ===
    cross-layer edge counts:
      workflows -> core      : 254
      core      -> domain    : 138
      domain    -> core      : 75
      workflows -> domain    : 21
    2-cycles among packages modules: 0
    domain->domain module edges: 2
       packages.domain.character.service -> packages.domain.world.service
       packages.domain.character.service -> packages.domain.world.service.ReferencedError
  ```
  ⇒ Lead 的「模块级图**无 2 环、无 3 环**」**属实**（我另测 3 环 = 0）。
  附带成本：导入 `packages.domain.character.service` 现在连带加载
  `packages.domain.world` / `.models` / `.service`（probe3 段 4）。
- **判别**：因为方向表里**没有** domain→domain 这一条，**任何**域间依赖都不受机器约束
  （将来把 domain 依赖引进 core、或 domain 之间互相依赖，都不会被守）。
  Lead 给的保留理由「域→域 import 无成文禁令」字面为真，但代价是这条边**从此没有任何看守**。
- **建议修法**：要么加一条 `domain → domain` 方向边 + 精确白名单（当前只有 1 条，成本极低），
  要么把共用错误类型下沉到中立位置（如 `packages/domain/_errors.py`）—— 后者更好：
  「删除被引用 ⇒ 409」是**跨域同义**的错误族，天然属于**共用基类**而不是任一域的子类。

## B 组

### B1 · **major** · `_artifacts_root()` 回退链的**最后一档就是软件仓自身** ⇒ 正是要防的落点

- **文件:行**：`scripts/eval_regression.py:38-52`（`_artifacts_root()` + `DOCS_RUNS_DIR`）、
  `scripts/m1_report.py:35-47`（`_artifacts_root()` + `DOCS_OUT_PATH`）。
- **证据**（把两个脚本复制到隔离目录 `advB-fallback`，其同级无 `NovelOS-Artifacts`）：
  ```
  === NOVELOS_ARTIFACTS_DIR unset, no sibling NovelOS-Artifacts -> fallback ===
    REPO_ROOT      = C:\...\advB-fallback
    sibling exists= False
    artifacts_root = C:\...\advB-fallback
    DOCS_RUNS_DIR  = C:\...\advB-fallback\docs\evaluation\runs
      -> inside software repo? True

  === m1_report.py fallback chain ===
    artifacts_root = C:\...\advB-fallback
    DOCS_OUT_PATH  = C:\...\advB-fallback\docs\evaluation\m1-data.md
    inside software repo? True
  ```
  设了 `NOVELOS_ARTIFACTS_DIR` 时正确指向痕迹仓 ✅（`D:\zcodeproject\NovelOS-Artifacts`）。
- **判别**：痕迹仓**没有 remote** ⇒ **克隆软件仓的人根本拿不到它** ⇒ 新克隆 / CI / 换机上
  `--write-docs` **必然**把含真实 `prj_`/`ch_` id 与正文片段的报告写回软件仓。
  `.gitignore` 只挡 git 提交，**挡不住落盘**（且 `git add -f` 可绕过）。
  docstring 写的取舍是「找不到就回落到软件仓（保持旧行为可跑）」—— 这与本次改动的
  目的（「必须落痕迹仓」）**直接冲突**。
- **建议修法**：最后一档改为**拒绝写 + stderr 明确警告**（或回退到系统临时目录）；
  「旧行为可跑」不构成把痕迹写回软件仓的理由。

### B2 · **major** · `eval_regression.py` 的默认 `--baseline` 仍指向软件仓，而基线文件已被移走 ⇒ **每次运行都走「无基线 → 建基线 → PASS」**，并把新基线写回软件仓（该路径无 gitignore 规则）

- **文件:行**：`scripts/eval_regression.py:94-97`（默认 `--baseline docs/evaluation/baseline/last_passing_run.json`，
  相对 CWD 解析）、`tests/evals/regression_baseline.py:74-85`（`load_baseline`：不存在 ⇒ None）、
  `:217-222`（`baseline is None` ⇒ `decision="PASS"` + `baseline_created=True`）、
  `:240-243`（PASS 且 created ⇒ `save_baseline(baseline_path, new_baseline)`）。
- **证据**：
  ```
  软件仓 docs/evaluation/baseline/ 内容：只有 .gitkeep
  git ls-files docs/evaluation/baseline/  => docs/evaluation/baseline/.gitkeep
  git log --all --diff-filter=A -- docs/evaluation/baseline/last_passing_run.json  => (空，从未被跟踪)
  痕迹仓：docs/evaluation/baseline/last_passing_run.json（run_20260825T075747476953）存在
  git check-ignore -v docs/evaluation/baseline/last_passing_run.json => NOT IGNORED
  ```
  真实仓实跑（把 `--baseline` 重定向到临时目录，避免写软件仓）：
  ```
  summary: 3/3 cases passed
  baseline: NONE
  baseline-created: 无既有基线，本次全绿结果已建为基线（PASS）
  decision: PASS
  ```
  ⇒ 基线文件被自动生成（`ls` 确认）。
- **判别**：① §6 结构漂移闸门从「与 2026-08-25 钉住的基线比对」退化为「与**上一次自产基线**比对」
  ⇒ **首次出现的漂移会被静默吸收**（建基线分支直接给 PASS）；② 引导写入软件仓**未 gitignore**的路径
  ⇒ 工作树变脏、可被误提交；③ `scripts/eval_regression.py:11-12` 的 docstring 仍称基线在仓库，
  已过期。
- **缓解（实测）**：新生成的基线与痕迹仓那份**逐 case 完全相同** ⇒ **今天没有真实漂移被掩盖**；
  且伏笔名等字符串本来就在 `tests/evals/golden/` 里 ⇒ 内容泄漏增量 ≈ 0。所以是 major 而非 critical。
- **建议修法**：`--baseline` 默认改为 `<artifacts_root>/docs/evaluation/baseline/last_passing_run.json`
  （与 `--write-docs` 同一套根目录解析），并在痕迹仓缺失时**拒绝建基线**而不是建到软件仓。

### B3 · **minor** · `docs/evaluation/README.md:29` 的「70+ 个 `run_*.json`」实测是 **68** 个

- **证据**：`find docs/evaluation -name "run_*.json" | wc -l` = **68**（整个痕迹仓也是 68）。
- **判别**：新文档里的数字当场就不准 ⇒ 与本仓「数字自己复算」纪律不符；这类数字会被人引用。
- **建议修法**：改成 68，或改成「60+」这种不随时间失效的写法。

### B4 · **minor** · `.gitignore` 新规则与既有规则重叠（`*.db.enc` / `*.tar.enc` 在 `data/` 下其实先命中 `data/` 规则）

- **证据**：`git check-ignore -v data/novelos.db.enc` ⇒ 命中的是 `.gitignore:14:data/`，
  **不是**新加的 `*.db.enc` ⇒ 新规则只在 `data/` 之外的路径生效。
- **判别**：不是缺陷（防回流结论仍成立），但说明新增规则里有一部分是**既有规则的子集**；
  将来若有人删掉 `data/` 那条规则，`*.db.enc` 这条会**静默接管**（行为不变），
  反之若有人以为 `*.db.enc` 是「加密备份的兜底」而删掉 `data/`，`data/` 下的备份仍被 `data/` 规则挡着
  ⇒ 结论安全，但规则的**冗余**本身应被登记（规则宁少勿滥）。
- **建议修法**：保留即可，但在 `.gitignore` 注释里写明「`data/` 下由既有 `data/` 规则覆盖，
  `*.db.enc` 是仓外路径的兜底」。

### B5 · **pass** · `.gitignore` 9 条新规则逐条实测全部命中

- **证据**（`git check-ignore -v` 逐条）：`docs/evaluation/m1-data.md` ⇒ `.gitignore:83`；
  `m1-long-run-report.md` ⇒ `:84`；`m2-verification.md` ⇒ `:85`；
  `docs/evaluation/m2-judge/judge_results.json` ⇒ `:82`（目录级规则，目录内文件同样命中 ✅）；
  `docs/evaluation/runs/README.md` ⇒ `:80`（**「目录内文件」这类形态也命中** ✅）；
  `docs/evaluation/runs/real-llm-minimax-m3-2026-08-24.md` ⇒ `:80`；
  `provider_dumps/x.json` ⇒ `:77`；`data/novelos.db.enc` ⇒ `:14`；
  而 `docs/evaluation/README.md` ⇒ **NOT IGNORED** ✅（机制文档应留仓）。
  `docs/evaluation/runs/.gitkeep` 的反选规则实测有效（`git check-ignore` rc=1，
  且 `git ls-files` 确认它仍被跟踪）✅。

### B6 · **pass** · 痕迹仓**确无 remote**；8 个文件只是**移出工作树**、**未移出历史**（README 已如实登记）

- **证据**：`git -C D:/zcodeproject/NovelOS-Artifacts remote -v` ⇒ **输出为空** ✅；
  `git log --oneline -5` ⇒ `996255d chore(痕迹仓): 迁入软件仓遗留的评测/评审运行痕迹…` ✅；
  工作树 `git status --porcelain` ⇒ 空 ✅。
  软件仓侧：`git ls-tree -r origin/master --name-only | grep "^docs/evaluation/"` **仍列全部 8 个文件**
  ⇒ **已在 GitHub 历史里，不可撤回**。`docs/evaluation/README.md:17-21` 如实写明了这一点 ✅。
- **判别**：README 的两处关键声称（无 remote / 已上云无法撤回）**都属实**。

### B7 · **pass** · `docs/evaluation/README.md` 无隐私残留

- **证据**：正则扫描 `prj_[0-9a-f]|ch_[0-9a-f]|char_[0-9a-f]|sk-|api[_-]?key|token|password|[0-9]{3}-[0-9]{2}-[0-9]{4}`
  ⇒ 唯一命中是第 18 行的描述性词语「逐章 token/评分」（不是令牌）✅。
- **补充事实**（供 Lead 知情）：被移走的 8 个文件里确有真实标识 ——
  `m1-data.md` 1 处、`m2-judge/judge_results.json` **5 处**、
  `runs/real-llm-minimax-m3-2026-08-24.md` **4 处**（形如 `prj_/ch_` 的 id），
  与 README 的描述一致 ⇒ 移出工作树这件事**做对了**，但**历史里仍在**。

## C 组

### C1 · **major** · 内容仓 push 护栏的**三步解除命令第 2 步是无效命令**（exit 129，pushurl 未删）

- **文件:行**：`D:/zcodeproject/NovelOS-Content/.git/hooks/pre-push:12-14`
  （注释里第 2 步 `git remote set-url --delete --push origin`）。
- **证据**（隔离仓 `advB-guard-2`，git 2.55.0，**未对内容仓做任何写操作**）：
  ```
  === [A] undo step 2 verbatim: git remote set-url --delete --push origin (no URL arg) ===
  usage: git remote set-url [--push] <name> <newurl> [<oldurl>]
     or: git remote set-url --add <name> <newurl>
     or: git remote set-url --delete <name> <url>
  EXIT=129
  --- config after ---
  remote.origin.pushurl no-push://novelos-content-push-disabled   <- 未删掉

  === [A2] correct form (with URL arg) ===
  git remote set-url --delete --push origin no-push://novelos-content-push-disabled
  EXIT=0   -> pushurl 已从 config 消失

  === [A3] step 3 form (replace) ===
  git remote set-url --push origin <newurl>   EXIT=0   -> pushurl 被替换
  ```
- **判别**：护栏**仍可逆**（第 3 步实测能替换 pushurl）⇒ 「可逆」这半条**成立**。但照注释逐字执行
  会在第 2 步报 usage 错；若用户以为第 2 步已生效而只跑 1+2 步，就停在
  「**钩子已删、pushurl 还在**」的半开状态——比原状更危险（少了一层）。
- **建议修法**：第 2 步改成带 URL 参数的正确形式，或**直接删掉第 2 步**（只留 1+3；
  第 3 步本身已能替换 pushurl）。

### C2 · **minor** · push 护栏的覆盖面清单（全部实测，隔离仓 + `--dry-run`）

- **挡得住**（exit ≠ 0）：
  | 命令 | 结果 |
  |---|---|
  | `git push origin master` | pushurl 解析失败，exit **128** |
  | `git push --no-verify origin master` | 同上，exit **128** |
  | `git -c core.hooksPath=/nonexistent push origin master` | 同上，exit **128** |
  | `git -c remote.origin.pushurl=<好URL> push origin master` | 同上，exit **128**（pushurl 是**多值**变量，git 取第一个，`-c` 追加在后面不生效） |
  | `git push --dry-run --mirror --no-verify origin` | exit **128** |
  | `git push <显式URL> master` | **钩子**挡（HOOK-BLOCKED），exit 1 |
  | `git push other master`（新 remote） | **钩子**挡，exit 1 |
- **挡不住**：
  | 命令 | 结果 |
  |---|---|
  | `git push --dry-run --no-verify <显式URL> master` | **EXIT=0，真实推送成功**（两层同时被绕过） |
  | `git config remote.origin.pushurl <好URL>`（不加 `--add` 的替换式改写） | 绕过 pushurl 层（钩子仍在） |
- **内容仓实况**：`git -C D:/zcodeproject/NovelOS-Content push --dry-run origin master` ⇒
  ```
  git: 'remote-no-push' is not a git command. See 'git --help'.
  fatal: remote helper 'no-push' aborted session
  PUSH_DRYRUN_EXIT=128
  ```
  钩子自检 ⇒ 打印中文说明 + `HOOK_EXIT=1` ✅。
- **判别**：**单条命令绕不过两层** ⇒ 与「防误操作」的定位相称；但 `--no-verify` + 显式 URL
  是两条命令就能真推 ⇒ **不能当安全边界用**。另：钩子与 pushurl 都在 `.git/` 下，
  **不受版本控制** ⇒ 换机 / 重新 clone 后**护栏不存在**（对「本地内容仓」定位可接受，但要登记）。
- **建议修法**：在钩子注释里把「两层各自能挡什么 / `--no-verify` + 显式 URL 能全绕过」写清楚
  （现在的注释说「即使有人用 `--no-verify` 绕过，origin 的 pushurl 也已被改掉」⇒ **这句不准确**，
  显式 URL + `--no-verify` 是能绕过的）。

### C3 · **minor** · D-15 只改了 AGENTS.md **一处**，实际消费方的 docstring 仍是绝对口径

- **文件:行**：`packages/core/genre/consumers.py:17`
  「不做题材内容判断——**题材正文与审核禁忌清单只进内容仓**。」
  ⇒ 这**正是**消费 `forbidden_words`（同文件 `:137`）与 `taboo_notes`（`:266`）的那个模块，
  它的 docstring 断言的东西**被自己的代码证伪**。
- **其它仍用绝对口径处**：`docs/roadmap/题材库-评估与落地计划-2026-09-13.md:67` 与 `:91`
  （「审核红线：只进内容仓」「题材内容只进内容仓」）、
  `CHANGELOG.md:4663`（「审核禁忌只进内容仓」）。
  `packages/core/genre/README.md:44` **自带**「本模块只承载结构化消费子集」限定，反而准确 ✅。
- **判别**：D-15 的目的是消除「字面绝对」口径；若只改 AGENTS.md 而留下**实际消费方**的
  docstring，读者（人或 agent）读到的仍是绝对口径 ⇒ **D-15 没有做完**。
- **建议修法**：把 `packages/core/genre/consumers.py:17` 改成 D-15 同款措辞；
  roadmap / CHANGELOG 按 AGENTS 硬规则 7 加「历史注记」横幅（日期 + 状态 + 被什么取代）。

### C4 · **minor** · AGENTS.md 对 schema 的引用**逐字一致** ✅，但「条目化」对 `taboo_notes` 不准确

- **证据**：schema `taboo_notes.description` 原文
  「题材禁忌要点（命中即 high severity 的一票关注点；**只承载机制可呈现的要点，题材正文/平台审核清单留在内容仓**）。」
  AGENTS.md 引作「只承载机制可呈现的要点，题材正文/平台审核清单留在内容仓」⇒ **逐字相同** ✅。
- **不准处**：schema 实测结构 —— `pacing.redlines` = `array of string`（maxItems 50 / maxLength 200）✅
  条目化；`StyleConstraints.forbidden_words` = `array of string`（maxItems 50 / maxLength 40）✅ 条目化；
  **`CriticRubric.taboo_notes` = `{"type": "string", "maxLength": 2000}`** ⇒ **自由文本，不是条目化**。
  AGENTS.md 写「条目化的 `forbidden_words` / `pacing.redlines` / `critic_rubric.taboo_notes` 要点」
  ⇒ 对第三项不成立。
- **建议修法**：改成「条目化的 `forbidden_words` / `pacing.redlines` + **文本化的**
  `critic_rubric.taboo_notes`（≤2000 字）」。

---

# 3. 我没能攻破的地方（第一批）

1. **删角色的引用守卫本身 —— 攻不破。** 独立探针（临时库 + 合成夹具）实测：
   引用检查**两端点都查**（`from` 端点抛错、`to` 端点也抛错）；
   **抛错时一个字节都没删**（`characters(a)=1`、`character_states(a)=1`、`relationships(a)=1`、
   `characters(b)=1` 全部不变）；无引用时删成功且 `character_states` 一并清掉；
   删不存在的角色返回 `False`（既有契约不变）。
   且 DB 层引用面**完备**：`REFERENCES characters` 全仓只有 0001 的 `character_states`
   （FK 仍在，被显式级联删）与 0001 的 relationships 两条（**已被 0022 摘掉**）⇒ 没有第三个引用源被漏查。
2. **`PRAGMA foreign_key_check` 报不出孤儿 —— 属实。** 实测孤儿行存在时 `foreign_key_check -> []`，
   且 `foreign_key_list(relationships)` 只剩 `(projects, project_id)` 一条 FK ⇒ FK 没了 ⇒ 检查无对象。
3. **错误族归属 —— 攻不破（无实际误吞）。** 实测 MRO：
   `char.ReferencedError → world.ReferencedError → WorldServiceError → Exception`；
   `issubclass(char, world)=True`、`issubclass(char, plot)=False`；
   `http_status` 两侧同为 409。
   路由层：characters.py:96 catch 自己的 ✅；worlds.py:48/78 catch world 的（会顺带 catch char 的，
   但 world 端点不会抛 char 错）；plots.py catch plot 的（**不** catch char/world ✅）。
   全仓**无** `except WorldServiceError` 兜底（grep 0 命中）、**无全局 exception_handler**
   ⇒ 409 不会被误扩；`type(exc).__name__` 只用于错误**文案**、不参与状态码分发 ⇒ 无同名分叉导致的 catch 错。
   ⇒ Lead 的保留理由（跨域同义 ⇒ 同一类型；路由层一个 except 覆盖两域）**在今天成立**。
   唯一残留是**结构层面**的（见 A7）：domain→domain 边不受任何机器约束。
4. **44 条白名单「逐条真精确」—— 攻不破。** 全部 44 条都对应仓内**真实存在的 import 语句**
   （`_actual_core_to_domain_imports()` 与白名单**集合相等**，missing=[] ghost=[]），
   全部指向**真实存在的模块文件**（44/44 可解析），无目录粒度、无通配符、无幽灵条目。
   「新增依赖必红」在**静态**形态上确实成立（两条负向用例实测能拦）。
5. **突变规格 t7 三条 —— 攻不破（全部能红）。** 临时副本 `advB-mut-1`（`packages/`+`database/`+
   两个测试文件已复制，`import packages.domain.character.service` 实测解析到副本路径）：
   - T7-M1（撤掉引用检查）⇒ **3 failed / 3 passed**
   - T7-M1b（只查 `from` 端点的半修）⇒ **1 failed / 5 passed**（正是 `to` 端点那条）
   - T7-M2（白名单放宽成整层放行）⇒ **3 failed / 31 passed**
   三个锚点在生产代码中**恰好命中 1 次**（grep 计数 = 1）。
6. **「模块级图无 2 环、无 3 环」—— 属实。** 全仓 packages 模块图：2 环 = 0，3 环 = 0。
7. **`packages → apps` 方向 —— 0 违规**（静态扫描实测 `[]`）。
8. **痕迹仓「无 remote」—— 属实**（`git remote -v` 空输出）。
9. **`.gitignore` 新规则防回流 —— 属实**（逐条 `git check-ignore -v` 全中，含目录内文件形态）。
10. **`docs/evaluation/README.md` 无隐私残留 —— 属实**（唯一命中是描述性词语「token」）。

---

# 4. 复核清单（第一批：实际跑过的命令与输出）

**只读核 / 探针（全部在系统临时目录 `C:\Users\aistation\AppData\Local\Temp\advB-2026-10-23\` 或
`advB-mut-1` / `advB-fallback` / `advB-guard-2` / `advB-guard-3` / `advB-e2e` 副本内进行）**

| # | 命令 / 动作 | 关键输出 |
|---|---|---|
| 1 | `sha256sum` + `wc -l` 快照（审查首尾各一次） | 除 AGENTS.md 外逐位一致 |
| 2 | `git diff packages/domain/character/{service,__init__}.py` | 删角色引用检查 + `ReferencedError` 子类 + `__init__` 导出 |
| 3 | `grep -rn "ReferencedError" --include=*.py` | world/plot/character **三处同名**；MRO 与 catch 点分布 |
| 4 | `grep -rn "REFERENCES characters" database/migrations/*.sql` | 仅 0001 两处（0022 已摘） |
| 5 | probe1.py（临时库 + 合成夹具） | 两端点都抛错 / 抛错零删除 / `foreign_key_check -> []` / **删 faction 留 1 条孤儿** / MRO 与 http_status |
| 6 | `grep -n "def resolve_fast_mode"` / `read_project_fast_mode` | 解析序 request > 项目列 > env > 关；NULL ⇒ 回落 env |
| 7 | `pytest tests/unit/test_character_delete_reference_guard.py tests/unit/test_no_core_to_workflows_dep.py -q --basetemp=<temp>/pt1` | **40 passed in 5.06s** |
| 8 | probe2.py（分层守卫四个扫描器 + 白名单 + 全仓 import 图） | 动态 domain import 全放行；domain 相对逃逸扫描器从未被调用；白名单 44=44 且无字面量 pin；domain→domain 2 条、2/3 环 = 0 |
| 9 | probe3.py（白名单逐条可解析 / apps 方向 / 3 环 / 传递依赖） | 44/44 可解析；apps 0；3 环 0；character 连带加载 world |
| 10 | mutate.py（临时副本 `advB-mut-1` 实跑三条突变） | T7-M1 3 failed / T7-M1b 1 failed / T7-M2 3 failed |
| 11 | `git check-ignore -v` × 12 条路径 | 新规则全部命中；`docs/evaluation/README.md` NOT IGNORED |
| 12 | `git log --all --diff-filter=A -- docs/evaluation/*` + `git ls-tree -r origin/master` | 8 个文件**仍在历史与 origin/master** |
| 13 | `git -C D:/zcodeproject/NovelOS-Artifacts remote -v` / `status` / `log` | **空输出**（无 remote）；工作树干净；`996255d` |
| 14 | `find docs/evaluation -name "run_*.json" | wc -l` | **68**（README 写 70+） |
| 15 | 隔离副本 `advB-fallback` 跑 `_artifacts_root()` | `inside software repo? True`（两脚本均是） |
| 16 | 真实仓 `scripts/eval_regression.py --baseline <temp> --runs-dir <temp>` | `3/3 passed` / `baseline: NONE` / `baseline-created` / `decision: PASS` |
| 17 | 新旧基线逐 case diff | **完全相同**（⇒ 今天无真实漂移被掩盖） |
| 18 | `git -C .../NovelOS-Content push --dry-run origin master` | `remote helper 'no-push' aborted session`，exit **128** |
| 19 | `sh .../NovelOS-Content/.git/hooks/pre-push; echo $?` | 中文说明 + `HOOK_EXIT=1` |
| 20 | 隔离仓 `advB-guard-2/3` 逐条试解除命令 + 7 种绕过形态 | 第 2 步 **EXIT=129**（pushurl 未删）；`--no-verify` + 显式 URL **EXIT=0 真推** |
| 21 | `python -c` 读 schema 三段结构 + AGENTS.md 引文比对 | 引文**逐字一致**；`taboo_notes` 是 string 非数组 |
| 22 | `grep -rn "只进内容仓\|审核红线"` 全仓 | 另 3 处仍用绝对口径（`consumers.py:17` 等） |

**未验证（环境/纪律边界）**：存量孤儿在 **dev 活库**中的实际条数 —— 按纪律「不读
`data/novelos.db`」，我只证伪了「FK 能报出来」并证明清理路径缺失，**未统计活库数量**。

---

# 5. 第二批：Task 5「前端快通道双写入口 + 状态机 hook 零覆盖」

## 5.1 结论摘要

- **驳倒的部分**：章节页「取消勾选」现在写 `fast_mode: null` ⇒ 对 `fast_mode=NULL` 的项目
  **UI 永远写不出「显式关」**（能力损失，相对 Task 5 之前是**回归**）；且
  `NOVELOS_FAST_MODE=1` 时 NULL ⇒ 后端有效值 = ON ⇒ 作者看到开关未勾选（甚至显示「未设置」），
  **后端却仍在跳过审校闸门** ⇒ **不安全方向的假承诺**。新增测试把这个行为**锁死**了。
- **没驳倒的部分**：「双入口库终态逐格相同」我逐格核过，**成立**（都是 NULL）——
  但成立的是「同为 NULL」，不是「同为显式关」，也不是「与 Task 5 之前等价」。
- **守住的**：快通道/auto_commit 的**串联**关系前端**如实表达**了（连勾都勾不上）；
  项目弹窗的「提交值 ≠ 当前值才下发」**没有被退化**；三条突变**全部能红**；
  新测试**不是自证**（断言打在真实值上，无恒真断言）。

## 5.2 发现清单（第二批）

### T1 · **major** · 章节页「取消勾选」改写 `fast_mode: null` ⇒ 对 NULL 项目 UI 写不出「显式关」，且在 env 生效时「关掉」不生效

- **文件:行**：`apps/web/src/pages/ChapterDetailPage.tsx:283-306`
  （`const baselineEffective = baseline === true;` /
  `const target = v === baselineEffective ? baseline : v;` /
  `await projectsApi.update(projectId, { fast_mode: target });`）。
- **后端口径**：`packages/core/api/routers/workflows/fast_mode.py:87-108`
  `resolve_fast_mode`（request > **项目列** > **env** > 关）；`:53-73` `read_project_fast_mode`
  对 NULL 返回 None ⇒ **NULL ⇒ 回落 env**。
- **证据（回归事实）**：Task 5 之前（HEAD 版）章节页发的是 `{ fast_mode: v }`（**布尔**）⇒
  对 NULL 项目「取消勾选」写的是 `false`（显式关）；Task 5 之后写的是 `null`。
  仓内新增测试**自己锁定了新行为**：
  `apps/web/src/pages/ChapterDetailPage.test.tsx`「项目未设置时：勾选 ⇒ true；再取消 ⇒ 恢复未设置
  （fast_mode: null，与项目弹窗同终态）」⇒ `expect(second.fast_mode).toBe(null)`。
  突变验证（临时副本 `advB-web4`）：把 `const target = v === baselineEffective ? baseline : v;`
  改回 `const target = v;` ⇒ **该用例红**（`expected false to be null`）⇒ 新行为确实被断言钉住。
- **判别**：
  ① **能力损失（无条件）**：项目列为 NULL 时，章节页开关的目标值集合是 `{null, true}`，
     **永远发不出 `false`** ⇒ 「显式关」这一态在章节页不可达。
  ② **安全性（条件性但真实）**：`NOVELOS_FAST_MODE=1` 时 NULL ⇒ 有效值 ON ⇒
     作者看到开关**未勾选**、文案「未设置（后端按环境变量回落）」，**后端却仍在快通道**
     （跳过审校闸门）⇒ 这是**不安全方向**的假承诺。
  ③ **仓内已有的记录被推翻**：`apps/web/src/pages/ProjectsListPage.test.tsx:267` 的注释写着
     「与章节页开关（发 `{fast_mode: v}`，**能关**）语义相反」—— 那句「能关」的能力
     **被 Task 5 删掉了**，而弹窗侧其实也一直写不出「显式关」（见 T5）。
- **建议修法**：**不要从布尔开关发 `null`**。用户点「关」= 显式关 ⇒ 发 `false`。
  「恢复未设置」若要做，应当是**独立的显式控件**（例如「恢复默认（未设置）」按钮），
  并且**两个入口都要有**；否则三态里的第三态永远不可达。

### T2 · **minor** · 「库终态两侧逐格相同」我逐格核过，**成立**（但成立的是「同为 NULL」）

- **逐格矩阵**（读码 + 仓内既有/新增断言；章节页 = 即时 PATCH，弹窗 = 批量表单）：

  | 项目列基线 | 用户动作 | 章节页 PATCH 序列 | 项目弹窗 PATCH | DB 终态 | 一致？ |
  |---|---|---|---|---|---|
  | NULL | 开 | `{true}` | `{fast_mode:true}` | 1 | ✅ |
  | NULL | 开→关 | `{true}`→`{null}` | 省略该键 | NULL | ✅ |
  | NULL | 开→关→开 | `{true}`→`{null}`→`{true}` | `{fast_mode:true}` | 1 | ✅ |
  | true | 关 | `{false}` | `{fast_mode:false}` | 0 | ✅ |
  | false | 开 | `{true}` | `{fast_mode:true}` | 1 | ✅ |

  弹窗口径出处：`apps/web/src/pages/ProjectsListPage.tsx:470-472`
  `if (fastMode !== (initial?.fast_mode === true)) { payload.fast_mode = fastMode; }`
  （`initial` = 弹窗打开时的项目值；表单比较的是**最终勾选态 vs 打开时的值**）。
  ⇒ **我没有驳倒「终态逐格相同」这一条**；我驳倒的是「这个终态安全 / 与 Task 5 之前等价」。
- **建议**：既然「逐格相同」成立，就把这条**矩阵本身**写成一张断言表（而不是只钉其中一格），
  否则下一任改基线语义的人会以为「null 与 false 可以互换」。

### T3 · **major** · 前后端口径不一致**不只是显示问题**：它是「安全闸门被跳过而作者不知情」

- **文件:行**：后端 `packages/core/api/routers/workflows/fast_mode.py:87-108`；
  前端 `apps/web/src/pages/ChapterDetailPage.tsx:262-268`（`fastMode = fastModeValue === true` ⇒
  NULL ⇒ false）、`apps/web/src/pages/chapter/ChapterPipelineHeader.tsx:395,401`
  （`disabled={submitting || !fastMode}`）、`:351`
  （`auto_commit: b.action === 'write' && fastMode ? autoCommit : undefined`）。
- **判别**：项目列 NULL 且 `NOVELOS_FAST_MODE=1` 时，
  **后端已在快通道（跳过审校闸门）**，前端却显示「未设置（后端按环境变量回落）」、
  开关未勾选、auto_commit 入口被禁用 ⇒ 作者**无从得知审校闸门正在被跳过**。
  这与 AGENTS 坑区那条「静默降级」是同族：**未知的强度被当成合格**。
- **裁决建议（对 Lead 的明确回答）：应当加一个「已解析有效值」的只读出口，而不是停在保守显示。**
  - **最小实现**：复用现成的单点 `resolve_fast_mode(request_value=None, project_id=...)`
    （已在 `packages/core/api/routers/workflows/fast_mode.py:87`），
    在 **Project 读模型**上加 `fast_mode_effective: bool`（可选再加
    `fast_mode_source: 'project'|'env'|'off'`），或加一个
    `GET /api/projects/{id}/effective-settings`。改动面很小（后端 ~5 行 + 前端读一个字段）。
  - **收益**：① 前端能把「未设置（**当前生效：开**）」说清楚 ⇒ 消掉**不安全方向**的假承诺；
    ② auto_commit 的可用性判断可以按**有效值**而不是按列值来做（现在在 env=1 时反而把
    用户能用的功能禁掉了）；③ 写路径的第三态（显式关）才有意义（T1 的根因）。
  - **若本批不加端点**：保守做法（禁用 auto_commit）**可接受**（fail-closed），
    但文案必须从「后端按环境变量回落」升级成**明确警告**：
    「未设置；若环境变量 `NOVELOS_FAST_MODE=1` 则**当前生效为开启**，审校闸门会被跳过」。

### T4 · **minor** · 快通道 / auto_commit 的**串联**关系前端**如实表达** ✅，但有两个残留状态问题

- ✅ **守住了**：auto_commit 复选框在 `!fastMode` 时 `disabled`（`ChapterPipelineHeader.tsx:401`），
  载荷也不带 `auto_commit`（`:351`）⇒ 「勾了 auto_commit 但 fast_mode 关着 ⇒ 毫无效果」
  这一情形在 UI 上**不可达**（用户连勾都勾不上）。AGENTS 硬规则 10 的串联语义**被如实表达**。
- ⚠️ **残留 1**：`autoCommit` 的 React state 在快通道关掉后**仍是 true**
  ⇒ 复选框显示成**「已勾选 + 禁用」**（禁用+勾选是公认的 UX 反模式，用户无法判断它会不会生效）。
  Task 5 的新测试把这个状态写进了断言（注释原文：「auto_commit 的勾选态仍留在 true（控件只是被禁用）」）。
- ⚠️ **残留 2（既有行为，Task 5 未引入，但新增测试把它锁定了）**：
  `ChapterDetailPage.tsx:283` `if (v && !autoCommit) setAutoCommit(true);`
  ⇒ **打开快通道会自动勾上「写完直接提交（跳过审校与审批）」**。一个开关顺带打开了另一个
  「跳过审校与审批」的开关且无任何提示 ⇒ 建议 Lead 明确裁决（拆成两个独立开关，
  或在勾选快通道时提示）。
- **建议修法**：快通道关掉时把 `autoCommit` 一并置 false（或在 UI 上标注「已挂起」）；
  「开快通道 ⇒ 自动开自动提交」若保留，请在文案里写明。

### T5 · **minor** · 项目弹窗的「提交值 ≠ 当前值才下发」**没有被 Task 5 退化** ✅；但两侧共有同一个缺口

- **文件:行**：`apps/web/src/pages/ProjectsListPage.tsx` **未被本次改动触及**
  （`git status` 未列该文件；sha256 `39ca1bb2237b6d4f` / 703 行）。
- **「由开改关 ⇒ 真的下发 false」路径实测仍在**：`:470-472`
  `if (fastMode !== (initial?.fast_mode === true)) { payload.fast_mode = fastMode; }`
  （初始 true ⇒ 取消勾选 ⇒ `false !== true` ⇒ 下发 `false` ✅），
  且有既有回归看守 `apps/web/src/pages/ProjectsListPage.test.tsx:305-326`
  「快通道-编辑：项目已开启时取消勾选必须下发 false（回归看守）」。
- **共有缺口**：项目列为 NULL 时，**弹窗与章节页都写不出显式关** ——
  弹窗：`initial.fast_mode === null` ⇒ `initial?.fast_mode === true` 为 false ⇒ 复选框未勾选 ⇒
  用户只能提交 `true`；章节页：目标集合 `{null, true}`（T1）⇒
  **两入口在 NULL 项目上都没有「显式关」这个出口** ⇒ 这是**接口层面**的缺口，不是前端 bug。
- **建议修法**：与 T3 合并处置 —— 写路径要能表达第三态（显式控件），或由后端提供
  「以显式关覆盖」的写入语义。

### T6 · **minor** · 新测试**不是自证**：断言打在真实值上、无恒真断言；但有一处把已知 bug 写成了期望行为

- **证据**：7 个测试文件逐个 grep，`expect(true)` / `toBeTruthy()` 计数**全为 0**；
  `ChapterDetailPage.test.tsx` 断言的是 `projectsApi.update` **收到的 payload 实际内容**
  （`payload.fast_mode` / `'auto_commit' in payload`），不是「元素存在」；
  `usePoll.test.ts` 断言 `fn.mock.calls.length`（**实际调用次数**）⇒ 「不自动重试」这条不变量
  是真断言；`App.test.tsx` 断言路由落到哪个组件（改任一 path 必红）。
- ⚠️ **唯一问题**：`apps/web/src/hooks/useChapterRunOrchestration.test.ts:132`
  `if (!id) return running; // 挂载时 detail 的空 id 首拉，不计入`
  ⇒ **把「GET /runs/null」这个已知 bug 写进了夹具**；而且首条断言
  `await waitFor(() => expect(result.current.detailRun?.status).toBe('RUNNING'))`
  **恰恰依赖**这次空 id 首拉（否则 `detail.data` 初始为 null）⇒
  **将来修掉空 id 拉取，这条测试会假红**。
- **建议修法**：先修 T7（空 id 首拉），再把该夹具改成「`selectedRunId` 为空时 `detail` 不请求」，
  首条断言改为 `expect(result.current.detailRun).toBeNull()`。

### T7 · **minor** · `GET /runs/null`（挂载时的空 id 首拉）确认存在，且不被任何测试当作缺陷

- **文件:行**：`apps/web/src/hooks/useChapterRunOrchestration.ts:90-93`
  `useApiCall<WorkflowRun>(() => workflowsApi.get(selectedRunId as string), [selectedRunId])`
  —— `selectedRunId` 初值 `null`，`as string` 是**类型谎言**；
  `apps/web/src/api/endpoints.ts:426-427` `api.get('/runs/' + runId)` ⇒ **`GET /api/runs/null`**；
  `apps/web/src/hooks/useApiCall.ts:40-46` `useEffect` **无条件** `fn()`，无 null 守卫 ⇒ 必然发出。
- **判别**：每次进入章节页多一次必然 404 的请求；且 404 可能被 `detailError` 渲染到
  时间线区（`ChapterRunPanel` 里 `detailError` 优先于 `pollError`）⇒ **在「轮询错误」横幅旁边
  还会冒出一条误导性的详情错误**。
- **建议修法**：`fn` 改为 `selectedRunId ? workflowsApi.get(selectedRunId) : Promise.resolve(null)`，
  或给 `useApiCall` 加 `enabled` 开关（后者更通用，仓内其它 `useApiCall` 调用点也可能受益）。

### T8 · **minor** · 「ChapterDetailPage.tsx 已 3418 行」这一**前提不成立**

- **实测**：`apps/web/src/pages/ChapterDetailPage.tsx` = **1135 行**（本次改动前 1074 行，**+61**）；
  **3418 行是测试文件** `apps/web/src/pages/ChapterDetailPage.test.tsx`。
  AGENTS.md 硬规则 1 的原文就是「`ChapterDetailPage.test.tsx` **3418**（全仓最大）」，
  归在「另两个是**测试文件**且从未拆过」那一档。
- **判别**：本次**没有新增超 1500 行的源文件**（1135 < 1500 ✅）；但测试文件从 **3418 → 3689**（+271）
  ⇒ **既有挂账被加剧**，未新增违规。
- **建议（明确回答「本批该不该拆」）**：**不必**在本批拆源文件（它没破线）。
  但建议**单独立一条挂账**：把 `ChapterDetailPage.test.tsx` 按 `describe` 块拆成
  `pages/chapter/*.test.tsx`（它是全仓最大文件，且每次改这个页面都要连坐 3689 行；
  本次 Task 5 已经在往它尾部堆第 3 个 `describe` 块了 ⇒ 再不拆，下一批会继续堆）。
  ⚠️ 拆分要遵守 AGENTS 硬规则 1 的 patch 契约纪律：**先 grep `monkeypatch`/mock 打点**，
  避免拆完测试打点失效。

### T9 · **minor** · 突变验证（等效实测，临时副本 `advB-web4`）三条全红 ✅

- **手法**：把 `apps/web`（含 `node_modules`）整目录复制到
  `C:\Users\aistation\AppData\Local\Temp\advB-web4`，**在副本里**注入突变、跑指定测试、跑完还原。
  **仓库文件全程未被改动。**
- **M-A**（子代理「突变 E」）：
  `auto_commit: b.action === 'write' && fastMode ? autoCommit : undefined` → 去掉 `&& fastMode`
  ⇒ **红 1 条**，正是
  「auto_commit 载荷链：快通道开过再关 ⇒ startWrite 载荷仍不含 auto_commit」
  ⇒ **子代理的「开过再关」改法确实能红**（第一版「从未开过」确实抓不住，因为那时
  `autoCommit` 本来就是 false）。✅
- **M-B**：`const target = v === baselineEffective ? baseline : v;` → `const target = v;`
  ⇒ **红 1 条**（「再取消 ⇒ fast_mode: null」）⇒ 新行为被断言钉住（也说明 T1 的能力损失被测试锁死）。
- **M-C**：`? 'unset'` → `? 'off'` ⇒ **红 1 条**（「项目值为 null ⇒ 显示『未设置』」）
  ⇒ 三态文案确有判别力。
- **基线**：副本内 `-t "快通道"` **10 passed**；原仓 `src/pages/ChapterDetailPage.test.tsx`
  全量 **92 passed / 92**；新文件 6 个 **49 passed / 49**。

## 5.3 我没能攻破的地方（第二批）

1. **「库终态两侧逐格相同」—— 攻不破。** 逐格矩阵（见 T2）全部一致 ⇒ 该声称**成立**。
   我原本预期会在 (NULL → 开 → 关) 这格找到分歧（章节页发 `null`、弹窗省略键），
   实测两者 DB 终态都是 NULL ⇒ **没有分歧**。
2. **快通道 / auto_commit 的串联语义被前端如实表达 —— 攻不破。**
   `disabled={submitting || !fastMode}` + 载荷不带 `auto_commit` ⇒ 「勾了却无效」的形态
   **UI 上不可达**（连勾都勾不上）。
3. **项目弹窗的「取消勾选必须真的落库」没有被退化 —— 攻不破。**
   弹窗文件**未被本次改动触及**；`true → false` 路径实测仍下发 `false`，且有既有回归看守。
4. **新测试不是自证 —— 攻不破。** 7 个文件里 `expect(true)` / `toBeTruthy()` 全为 0；
   断言打在 payload 实际内容 / 实际调用次数 / 实际路由落点。
5. **`usePoll.retry()` 的实现 —— 攻不破。** `retry()` = `setError(null)` + `setNonce(n+1)`，
   而 polling effect 的依赖含 `nonce`（`usePoll.ts:158`）⇒ effect 清理旧 timer → 重跑 → **立刻再拉一次**；
   旧闭包的 `stopped=true` 不会污染新一轮（新一轮是新的 `let stopped = false`）。
   三条新用例（停摆不自动重试 / retry 恢复 / retry 后再报错）都是真断言。
6. **`useChapterRunOrchestration` 的 M-21 补口 —— 攻不破。**
   「PAUSED ⇒ detail 重拉 ⇒ pausePayload 出现」是**真判别点**：
   把 `if (latest.status === 'PAUSED') void detail.reload();` 删掉 ⇒ `pausePayload` 永不出现
   ⇒ 该用例必红（夹具按调用次序返回 RUNNING/RUNNING/PAUSED/PAUSED，
   只有重拉才会拿到 PAUSED 版）。runs 过滤/排序/默认选中/切换选中、qualityGateCheckpoint 四类
   降级也都有负向用例。⚠️ 但它**只覆盖 hook 自身的职责**（拉取、选中、轮询、checkpoint 提取）；
   **pause/resume 的动作本身不在这个 hook 里**（在 `ChapterDetailPage.tsx` 的
   `handleStartWorkflow`），所以「run 暂停/恢复状态机」**没有**被这个 hook 测试覆盖 ——
   这是 M-21 的真实边界，不是虚假声称。
7. **AGENTS.md 的 D-15 措辞 —— 攻不破。** AGENTS.md 的引文与 schema `description` **逐字一致**
   （已在 C4 逐字比对）。

## 5.4 复核清单（第二批：实际跑过的命令与输出）

| # | 命令 / 动作 | 关键输出 |
|---|---|---|
| 1 | `sha256sum` + `wc -l`（14 个文件，审查首尾各一次） | 全部逐位一致 |
| 2 | `git diff` × 4 个实现文件 | 三态基线逻辑、`retry()`、`pollRetry`、错误横幅与三态文案 |
| 3 | `git diff` × 2 个改动的测试文件 | 快通道 10 例 + 轮询 3 例 |
| 4 | `grep -rn "fast_mode\|fastMode\|auto_commit" apps/web/src` | 两个写入口 + 三态文案 + auto_commit 禁用链 |
| 5 | 读 `ProjectsListPage.tsx:375-481` | 弹窗口径：`if (fastMode !== (initial?.fast_mode === true)) payload.fast_mode = fastMode` |
| 6 | 读 `packages/core/api/routers/workflows/fast_mode.py:53-115` | 解析序 request > 项目列 > env > 关；NULL ⇒ env 回落 |
| 7 | `grep -rn "NOVELOS_FAST_MODE"` 全仓 | 仅代码/文档，**仓内无处设置** ⇒ env 危害为**条件性** |
| 8 | `npx vitest run` × 6 个新测试文件 | **6 files / 49 passed** |
| 9 | `npx vitest run src/pages/ChapterDetailPage.test.tsx` | **92 passed / 92** |
| 10 | `grep -c "expect(true)\|toBeTruthy()"` × 7 个测试文件 | 全部 **0** |
| 11 | 读 `useApiCall.ts:36-52` | `useEffect` 无条件 `fn()` ⇒ 空 id 首拉必然发出 |
| 12 | `grep -n "router.get(\"/runs/"` | 未在 `workflows/*.py` 找到（路由由 `__init__.py:9` 记录）⇒ 空 id 路径的 404 行为**未直接实测**（见下） |
| 13 | 复制 `apps/web` → `advB-web4`（含 node_modules），`npx vitest run -t "快通道"` 基线 | **10 passed** |
| 14 | mutate 脚本 M-A / M-B / M-C（**仅在副本里**注入，跑完还原） | 三条全部 **RED**（各 1 failed） |
| 15 | `git -C D:/zcodeproject/NovelOS-Content ...`（只读 + `--dry-run`） | pushurl 挡 exit 128；钩子 exit 1 |
| 16 | `sha256sum AGENTS.md` 复查 | `4d989eb9…`/391 → `6e7c11bd…`/412（Lead 改动）；硬规则 5 第 71 行**逐字未变** |

**未验证（环境/纪律边界）**：
① `GET /api/runs/null` 的**后端响应码**未直接实测（路由文件未定位到；`useApiCall` 无 null 守卫
   ⇒ 请求必然发出这一点已由代码证实，响应码 404/500 的差别不影响结论方向，但本报告**不声称**已核实）；
② 未跑前端**全量**套件（并发纪律：只跑指定文件）。

---

# 6. 给 Lead 的裁决要点（第二批）

| 议题 | 本审查结论 | 建议裁决 |
|---|---|---|
| 章节页「取消勾选」发 `null` | **驳倒**（能力损失 + env 下不生效 + 测试锁死） | 改为发 `false`；「恢复未设置」另做显式控件 |
| 「双入口终态逐格相同」 | **成立**（攻不破） | 把矩阵本身写成断言表；别让它被下一次改动悄悄改形 |
| 前后端口径不一致 | **驳倒「只是显示问题」** | **加只读「已解析有效值」**（复用 `resolve_fast_mode`）；不加则文案升级成明确警告 |
| 快通道 / auto_commit 串联 | **守住了** | 残留：关掉快通道时把 `autoCommit` 一并置 false |
| 项目弹窗退化 | **没有退化** | NULL 项目的「显式关」缺口两侧共有，随第三态控件一起解决 |
| 突变验证 | **三条全红** | 无需补 |
| `ChapterDetailPage.tsx` 行数 | **前提不成立**（源 1135；3689 是测试） | 本批**不必**拆源文件；单独立「拆测试文件」挂账 |


---

# 7. 追加小节：Task 6「三仓边界与零覆盖模块」的落地件 `scripts/check_content_repo_guard.py`

> **快照时间**：2026-10-23 12:44~12:52（本次追加复核时的工作树）。
> 本小节只做**只读核**：未对内容仓做任何写操作，未调用 `push` / `fetch` / `commit` / `add` / `reset`。
> 该脚本是 Lead 在 Task 6 之后的产出；**Task 6 未被指派给我**，本小节只核它与本报告
> **C1 / C2** 两条结论的交互，不对其整体质量下结论。

## 7.1 脚本行为（实测）

```
$ python scripts/check_content_repo_guard.py            # 退出码 0
  内容仓: D:\zcodeproject\NovelOS-Content
  fetch URL: https://github.com/charlesnty612/NovelOS-Content.git
  push  URL: no-push://novelos-content-push-disabled
  pre-push 钩子: ...\.git\hooks\pre-push
  本地领先远端: 17 个提交
  结论: 不能被误推
  屏障: push URL 已禁用 / 存在 pre-push 钩子

$ python scripts/check_content_repo_guard.py --json     # 退出码 0
  "push_disabled": true, "pre_push_hook_present": true,
  "can_be_pushed": false, "verdict": "no",
  "barriers": ["push URL 已禁用", "存在 pre-push 钩子"], "ahead_count": 17
```

=> **Lead 预告的那点属实**：哨兵 `no-push://novelos-content-push-disabled` 现在被
`DISABLED_PUSH_PREFIXES`（`no-push:` / `nopush:` / `no_push:` / `disabled:`）**正确认成
「已禁用」**（脚本 docstring 明写「本机实际安装的哨兵是
`no-push://novelos-content-push-disabled`，**必须**认得」）。
=> 本报告 **C 组**结论**不受 Task 6 影响**：护栏两层都真的在位（exit 0，两条屏障都被识别）。

## 7.2 该巡检脚本**不是自证**（判别力实测，临时仓）

用 6 个一次性 git 仓（`C:\...\Temp\advB-guardchk\`）逐格喂：

| # | 夹具 | 退出码 | `can_be_pushed` | 判读 |
|---|---|---|---|---|
| A | 真 pushurl、**无**钩子 | **1** | **True** | OK 正确判「不安全」 |
| B | 真 pushurl、有钩子 | 0 | False | OK 钩子算屏障 |
| C | 哨兵 pushurl、无钩子 | 0 | False | OK 哨兵算屏障 |
| D | 哨兵 pushurl、有钩子 | 0 | False | OK |
| E | **无 pushurl**、无钩子 | **1** | **True** | OK 正确（无 pushurl => push 回落到 fetch URL） |
| F | 无 pushurl、有钩子 | 0 | False | OK |
| — | **真实内容仓** | 0 | False | OK 两屏障都在 |

=> **脚本有判别力**：撤掉任一屏障它会变红（A/E 两格），两屏障都在它给绿。
但它**只查屏障的「存在」**，不查屏障能否被联合击破（见 7.3）。

## 7.3 与本报告 C1 / C2 的交互：**两条都仍然成立**

### 7.3.1 **C1（解除命令 #2 无效）—— 仍然成立，脚本没有覆盖它**

- 内容仓钩子**未被改动**：`sha256 = 8fb2ad04f9e2cbe1c4ca60e5468d229c772fbfdb774ac42a6220fe180ea7a055`，
  第 13 行仍是 `git -C "..." remote set-url --delete --push origin`（**无 URL 参数 => exit 129**）。
- 判别：`check_content_repo_guard.py` 只回答「当前能不能被误推」，**不检查钩子注释里的
  解除步骤是否可执行** => 这条缺口在 Task 6 之后**依然存在**。

**实测过的可用解除步骤**（在一次性仓 `advB-guard-2` 上实跑过，**未对内容仓执行**）：

```
# (1) 拆钩子：删掉文件  D:/zcodeproject/NovelOS-Content/.git/hooks/pre-push

# (2) 删掉 pushurl —— 必须带 URL 参数，否则 exit 129、什么也没删
git -C "D:/zcodeproject/NovelOS-Content" remote set-url --delete --push origin \
     no-push://novelos-content-push-disabled          # 实测 EXIT=0，pushurl 从 config 消失

# (3) 指向真实远端
git -C "D:/zcodeproject/NovelOS-Content" remote set-url --push origin \
     https://github.com/charlesnty612/NovelOS-Content.git   # 实测 EXIT=0，pushurl 被替换
```

- **步骤 (2) 可以省略**：第 (3) 步本身是「替换」语义（实测 `set-url --push origin <new>` 会覆盖
  旧 pushurl，EXIT=0）=> 最小可用两步 = **(1) + (3)**。
- 建议把钩子注释的第 2 步**直接删掉**（避免照抄者撞 usage 错、误以为已解除一半）。

### 7.3.2 **C2（护栏覆盖面）—— 仍然成立，脚本的措辞需要收窄**

- 脚本结论「**不能被误推**」（`verdict: "no"`）在本报告 C2 实测下**偏强**：
  `git push --no-verify <显式URL> master` **两层同时被绕过且真实推送成功**（隔离仓实测 EXIT=0）。
  脚本只检查两屏障的**存在**，因此在该状态下它**仍会报绿**。
  => 这不是脚本的 bug（它回答的是「当前有没有技术屏障」），
  但**人读形态的措辞**「不能被误推」容易被读成「推不出去」=> 建议改成
  「**误 push 会被拦住**（有技术屏障）；**刻意绕过**（`--no-verify` + 显式 URL / 换 pushurl）
  仍可发布」——与 C2 的实测清单逐条对齐。
- **另一处盲区（minor，建议补）**：脚本只读 `git remote -v` 的**第一个** remote；
  **新增第二个 remote**（如 `git remote add backup https://github.com/...`）后，
  `git push backup master` **不走 origin 的 pushurl**，只剩钩子挡（实测 `HOOK-BLOCKED`）
  => 加 `--no-verify` 即可真推，而**脚本仍报绿**。
  真实内容仓当前**只有 `origin` 一个 remote**（`git remote -v` 实测）=> **无现存暴露**，
  但巡检口径应覆盖「是否存在指向真实远端的其它 remote」。
- 脚本的只读性我已核：实际发出的 git 调用只有
  `rev-parse --is-inside-work-tree` / `remote -v` / `rev-parse --git-path hooks` /
  `rev-parse --abbrev-ref --symbolic-full-name @{u}` / `rev-list --count @{u}..HEAD`
  —— **全部只读**，无 fetch/push/commit/add/reset（`READONLY_GIT_SUBCOMMANDS` 里多列的
  `config` / `status` / `symbolic-ref` 目前并未被用到）。

## 7.4 追加小节的复核清单

| # | 命令 / 动作 | 关键输出 |
|---|---|---|
| 1 | `python scripts/check_content_repo_guard.py` / `--json` | exit 0；`push_disabled=true`、`hook=true`、`can_be_pushed=false`、`ahead_count=17` |
| 2 | `sha256sum .../NovelOS-Content/.git/hooks/pre-push` + `grep set-url` | `8fb2ad04...`；第 13 行**仍是**无 URL 参数的写法 => C1 仍成立 |
| 3 | `git -C .../NovelOS-Content config --get-all remote.origin.pushurl` | `no-push://novelos-content-push-disabled`（单条） |
| 4 | `grep "_run_git("` 全量枚举脚本的 git 调用 | 5 条，**全部只读** |
| 5 | probe_guard.py：6 个一次性 git 仓逐格喂脚本 | A/E => exit 1 `can_be_pushed=true`；B/C/D/F => exit 0；真实仓 => exit 0 |
| 6 | `git -C .../NovelOS-Content remote -v` | **只有 `origin` 一个 remote**（无第二 remote 暴露） |

---

# 8. 全局：本次审查**未能覆盖**的边界（诚实登记）

| 边界 | 原因 | 后果 |
|---|---|---|
| 存量孤儿行在 **dev 活库**中的实际条数 | 纪律「不读 `data/novelos.db`」 | 只证伪了「FK 能报出来」（`foreign_key_check -> []`）并证明**清理/检测路径缺失**；**未统计活库数量** => A4 的严重度按「可达 + 无检测」判定，未按「存量有多少」判定 |
| `GET /api/runs/null` 的**后端响应码** | 未定位到该路由的 handler 文件 | `useApiCall` 无 null 守卫 => **请求必然发出**已由代码证实；404/500 之别不影响 T7 结论方向，但本报告**不声称**已核实响应码 |
| 前端**全量**套件 | 并发纪律（只跑指定文件） | 只跑了 Task 5 的 7 个文件（49 + 92 用例全绿）；**未验证**其余前端文件未被本次改动带红 |
| 任何**外部信息**（文档站 / PyPI / 上游仓库） | 本轮禁止联网 | 本报告**不含**任何外部来源引用；所有结论只依据本地工作树与本地实测 |
| `packages/**` 之外的分层方向（`scripts/` / `tests/`） | 分层看守的扫描根只覆盖 `packages/**` | `scripts/` 若 import `packages.workflows` 不会被拦（现状未破，但**无看守**） |
