r"""架构依赖方向看守（V1.5 起；2026-10-22 批次二十二重写，同批次 dev-a 修复）。

守三条方向：

1. **`packages/core` 不得 import `packages/workflows`**（业务流程包 → core，方向单向）。
2. **`packages/domain` 不得 import `packages/workflows`**（领域层不得依赖流程组装层）。
3. **`packages/**` 不得 import `apps`**（库层不得依赖应用层）。

## 2026-10-23 task-7：第四条方向 —— **`core → domain` 是「受控例外」**

**口径（Lead 裁决，别说过头）**：

- ``core → domain`` **不是**「破口」——Lead 实测模块级图**无 2 环、无 3 环**；
  且成文规则（``AGENTS.md`` §二）**只**写了「core 不得 import workflows」，
  **并未**禁过 ``core → domain``。
- 真实问题是「**边界没定义清楚 + 零机器约束**」⇒ 处置 = **受控例外 + 机器看守**：
  - **允许** API 路由层（``packages/core/api/routers/**``）调领域服务
    ——路由只做「参数校验 + 调用 + 错误映射」，业务规则一律在 domain
    （``packages/domain/README.md`` 的「下游：``packages/core/api`` 路由」）；
  - **允许** 5 个机制层文件按**精确白名单**调领域服务（见
    ``ALLOWED_CORE_TO_DOMAIN_IMPORTS``）；
  - **任何新增的 ``core → domain`` 依赖都必须让本看守变红** ⇒ 需要时**先登记再写**。

**白名单粒度 = ``(文件相对路径, 被导入的模块路径)``**，精确到文件 + 具体导入；
**不许**「整个 ``packages/core`` 放行」、**不许**「按目录放行」——那会让本看守退化成
噪声源，且上面最后一条裁决就守不住了（判别见 ``test_core_to_domain_allowance_is_not_coarse``）。

⚠️ 文档口径两处曾自相矛盾（``AGENTS.md`` §二 vs ``packages/domain/README.md`` 的
「下游」段）：本文件是**机器约束**的落点；AGENTS.md 的措辞由 Lead 统一改
（建议措辞见 task-7 报告）。

## 2026-10-22 批次二十二：重写 + 修复

**背景**：旧实现（只看字面值）实测 7 种写法只拦 5 种——拆字符串的
``importlib.import_module("packages"+"."+"workflows")`` 与 ``__import__(...)`` 全漏，
而 ``packages/core/api/main.py`` 用的**正是**这种拆字符串形态（盲区成因）。

**重写后的判据（本文件当前实现）**：

- **静态 import**：AST 扫 ``Import`` / ``ImportFrom``（含 ``TYPE_CHECKING`` 块内、
  函数体内、类体内），**外加「import 父包 + 取子包」形态**
  （``from packages import workflows`` ⇒ 视作 ``packages.workflows``）；
  另有专门的「相对 import 逃出本包」断言（``level >= 包深度``）。
- **动态 import（规则 R，callee 无关）**：对**任何** ``ast.Call``，若其**任一实参**
  可常量折叠为**包含** ``packages.workflows`` 的字符串 ⇒ 违规（除非命中白名单）。
  ⇒ ``getattr`` / 别名 / from-import / ``functools.partial`` / ``exec`` / ``eval``
  **一次堵死**，不必逐个枚举 callee 语法。
- **不可判定**：仅当被调用者可解析为**已知 import callee**
  （``import_module`` / ``__import__`` / ``exec`` / ``eval``）且实参折不出时，
  才要求登记 ⇒ 不会把 ``log.warning(<不可折叠>)`` 这类普通调用误判。
- 两个白名单都是**精确集合相等**断言（不是只钉条数）。

⚠️ **规则 R 的窄化（本文件的实现口径，与 Lead 提议的「包含」有一处差别）**：
「包含」用的是**点分路径感知**的正则 ``(?<![\w.])packages\.workflows(?![\w])``
⇒ ``packages.workflows.chapter_write`` 命中，``notpackages.workflows`` **不**命中
（后者不是一条模块路径）。理由见「规则 R 会不会太宽」一节。

⚠️ **已知过宽面（如实登记，2026-10-22 对抗审查实测）**：规则 R 是 **callee 无关**的
⇒ **任何调用**只要实参折叠出该串就红，包括纯日志/纯比较：
``log.warning("about packages" + ".workflows")`` ⇒ **误判为违规**
（``assert x == "packages.workflows"`` 由字面量禁令拦，同样红）。
这是「宁可误报、不可漏报」的**必然代价**，当前树 **0 例**。
**处置**：遇到时**改措辞**（拆开写、或用 f-string 插值使其不可折叠），
**不要**为了过闸而往白名单里加条目（白名单只给装配入口）。

**为什么必须这样**：2026-10-22 实测，重写后的第一版**在树上就是红的**
（``2 failed``）——因为 ``_module_string_env`` 只收**模块级**绑定，而
``packages/core/api/main.py:215`` 的 ``_workflows_pkg = "packages" + "." + "workflows"``
在 ``create_app()`` **函数体内** ⇒ 折不出来 ⇒ ① 它被记成「不可判定」
② 它**永远命中不了**白名单（白名单形同虚设）。
⇒ 折叠器改为**全树**（不限作用域）+ 同名多值视为不可折叠。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "packages" / "core"
DOMAIN_ROOT = REPO_ROOT / "packages" / "domain"
ENTRY_FILE = CORE_ROOT / "api" / "main.py"

_TARGET_PKG_TOP = "packages.workflows"
_TARGET_PKG_PARENT = "packages"

#: 合法**动态** import 指向业务流程包的位置与**折叠后的目标**（精确匹配，不许前缀通配）。
#: 唯一合法用途 = 装配入口的插件发现（V1.5 约定）。
#: ⚠️ 目标必须**恰好**是 ``packages.workflows`` ⇒ 入口若改成
#: ``packages.workflows.chapter_write`` 之类，**不在**白名单 ⇒ 判违规。
#: ⚠️ 2026-10-23 task-7 A1：动态扫描已**泛化**到多目标前缀表（见
#: ``_DYNAMIC_TARGET_RULES``）⇒ 本白名单的语义随之泛化为「合法的**动态目标串**」，
#: 但**粒度不变**（精确匹配，不许前缀通配）。
ALLOWED_DYNAMIC_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {("packages/core/api/main.py", "packages.workflows")}
)

#: 动态 import 的实参**无法常量折叠**时的豁免，粒度 = **调用级**
#: （``(文件相对路径, callee 规范名)``）⇒ 同一文件里**别的**不可折叠动态 import
#: 不会被这条豁免顺带放过。
NON_FOLDABLE_DYNAMIC_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {("packages/core/api/routers/__init__.py", "importlib.import_module")}
)

#: 需要扫描的根（**core 与 domain 各跑一遍** ⇒ 不再是「半边看守」）。
GUARDED_ROOTS: tuple[str, ...] = ("packages/core", "packages/domain")

#: 依赖方向：(被扫描根, 禁止的模块前缀, 人类可读描述)。
#:
#: ⚠️ 2026-10-23 task-7 A7：新增**第五条** ``domain → domain``
#: （首条 domain→域 依赖 = character/service.py 复用 world 域的 ``ReferencedError``
#: 与共用守卫 ⇒ 方向表里必须有这条边，否则**任何**域间依赖都不受机器约束）。
LAYERING_DIRECTIONS: tuple[tuple[str, str, str], ...] = (
    ("packages/core", "packages.workflows", "core 不得依赖业务流程包"),
    ("packages/domain", "packages.workflows", "domain 不得依赖业务流程包"),
    ("packages", "apps", "库层不得依赖应用层 apps"),
    (
        "packages/core",
        "packages.domain",
        "core 机制层不得依赖领域层（受控例外见 ALLOWED_CORE_TO_DOMAIN_IMPORTS）",
    ),
    (
        "packages/domain",
        "packages.domain",
        "domain 域之间原则上不得互相依赖（受控例外见 ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS）",
    ),
)

#: **受控例外**：`domain → domain` 的精确白名单。
#:
#: ⚠️ 2026-10-23 task-7 A7：首条 domain→domain 依赖 = **character 域复用 world 域的
#:    「删除被引用」错误类型与共用守卫**（Lead 裁决：保留继承，不下沉错误类型）。
#:    方向表里没有这条边 ⇒ 任何域间依赖都不受机器约束 ⇒ 这里补上。
#: 粒度 = ``(文件相对路径, 被导入的模块路径)``，与其它白名单一致。
ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {
        # character/service.py → world/service.py：复用 ``ReferencedError``（错误语义
        #   跨域同义：都是「被引用 ⇒ 409」）+ 共用「删实体前查 relationships 端点」守卫
        #   （0022 之后 character 与 faction 是两类关系端点 ⇒ 同一道守卫，两域共用一份）。
        ("packages/domain/character/service.py", "packages.domain.world.service"),
    }
)

#: **受控例外**：`core → domain` 的精确白名单。
#: 粒度 = ``(文件相对路径, 被导入的模块路径)`` —— 精确到**文件 + 具体导入**。
#:
#: ⚠️ **不许**「整个 ``packages/core`` 放行」、**不许**「按目录放行」：
#:    那会让本看守退化成噪声源，且「任何新增的 ``core → domain`` 依赖必须变红」
#:    这条裁决就守不住了（判别见 ``test_core_to_domain_allowance_is_not_coarse``）。
#:
#: 登记规则（Lead 裁决 + 本仓「规则宁少勿滥，每条挂实证依据」）：
#:   ① 每条都要写清**为什么允许**；
#:   ② 新增导入必须**先登记**（否则门禁红）；
#:   ③ 删掉导入也要**同步删登记**（否则 ``..._matches_actual_imports`` 红）。
#:
#: 规模：**47 条 import 语句** = 机制层 5 文件 8 条 + API 路由层 19 文件 39 条
#: （Lead 实测口径「8 条 import 语句 / 5 个文件」= 机制层那部分；API 路由层的
#: 39 条由 Lead 的裁决「允许 API 路由层调领域服务」覆盖，同样逐条登记）。
#: 去重后 = **44 条唯一 ``(文件, 模块)`` 对**（机制层 8 + API 路由层 36）——
#: 同一文件里同一模块被 import 两次的（如 ``characters.py`` 的
#: ``packages.domain.project.service``）在白名单里是**同一条**。
ALLOWED_CORE_TO_DOMAIN_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {
        # ============ 机制层（core 内、非 API 路由层）：5 文件 8 条 ============
        # arc/service.py：弧线评审要按 project 取项目（项目归属/设定的唯一权威口径
        #   在 domain；core 侧不再复刻一份）。
        ("packages/core/arc/service.py", "packages.domain.project.service"),
        # delivery/evidence.py：取证包要「取最新 draft」的**共享解析单点**
        #   （draft_resolver 是全仓单点，避免各模块各写一份版本口径）。
        ("packages/core/delivery/evidence.py", "packages.domain.chapter.draft_resolver"),
        # delivery/evidence.py：取证包要列该项目的章节。
        ("packages/core/delivery/evidence.py", "packages.domain.chapter.service"),
        # delivery/evidence.py：取证包要读项目元信息。
        ("packages/core/delivery/evidence.py", "packages.domain.project.service"),
        # exporter/builder.py：外发投稿包要列章节（ChapterService.list_by_project）。
        ("packages/core/exporter/builder.py", "packages.domain.chapter.service"),
        # quality/service.py：质量评审要取「**被审的那一版** draft」⇒ 走共享解析单点，
        #   否则版本口径会在 quality 与 delivery 之间分叉。
        ("packages/core/quality/service.py", "packages.domain.chapter.draft_resolver"),
        # signing_check/service.py：签章检查要列章节。
        ("packages/core/signing_check/service.py", "packages.domain.chapter.service"),
        # signing_check/service.py：签章检查要校验项目存在。
        ("packages/core/signing_check/service.py", "packages.domain.project.service"),
        # ============ API 路由层：19 文件 39 条 ================================
        # 路由层是 core 内**唯一**被允许直接调领域服务的一层
        # （依据：packages/domain/README.md「下游：packages/core/api 路由」）。
        # 边界口径：路由只做「参数校验 + 调用 + 错误映射」，**业务规则一律在 domain**。
        # characters：角色 CRUD + 路由内的 project 存在性校验。
        ("packages/core/api/routers/characters.py", "packages.domain.character.models"),
        ("packages/core/api/routers/characters.py", "packages.domain.character.service"),
        ("packages/core/api/routers/characters.py", "packages.domain.project.service"),
        # chapters：章节 CRUD + 路由内的 project 存在性校验。
        ("packages/core/api/routers/chapters.py", "packages.domain.chapter.models"),
        ("packages/core/api/routers/chapters.py", "packages.domain.chapter.service"),
        ("packages/core/api/routers/chapters.py", "packages.domain.project.service"),
        # continuation：续写端点要读章节 + 项目。
        ("packages/core/api/routers/continuation.py", "packages.domain.chapter.service"),
        ("packages/core/api/routers/continuation.py", "packages.domain.project.service"),
        # export：外发端点要读项目（导出范围按 project 裁剪）。
        ("packages/core/api/routers/export.py", "packages.domain.project.service"),
        # genre：题材包端点要读项目（题材包按 project 绑定）。
        ("packages/core/api/routers/genre.py", "packages.domain.project.service"),
        # ledger：台账端点复用 domain.ledger 的领域服务。
        ("packages/core/api/routers/ledger.py", "packages.domain.ledger"),
        # plots：情节/关系端点复用 domain.plot 的领域服务与出参模型。
        ("packages/core/api/routers/plots.py", "packages.domain.plot"),
        ("packages/core/api/routers/plots.py", "packages.domain.plot.models"),
        # projects：项目 CRUD 的出参模型与领域服务。
        ("packages/core/api/routers/projects.py", "packages.domain.project.models"),
        ("packages/core/api/routers/projects.py", "packages.domain.project.service"),
        # quality：质量评审端点要读章节 + 项目。
        ("packages/core/api/routers/quality.py", "packages.domain.chapter.service"),
        ("packages/core/api/routers/quality.py", "packages.domain.project.service"),
        # reference：参考资料端点复用 domain.reference 的领域服务与模型。
        ("packages/core/api/routers/reference.py", "packages.domain.project.service"),
        ("packages/core/api/routers/reference.py", "packages.domain.reference"),
        ("packages/core/api/routers/reference.py", "packages.domain.reference.models"),
        # reveal_policies：揭示策略端点复用 domain.knowledge 的领域服务与模型。
        ("packages/core/api/routers/reveal_policies.py", "packages.domain.knowledge"),
        ("packages/core/api/routers/reveal_policies.py", "packages.domain.knowledge.models"),
        # simulation：模拟端点要读项目。
        ("packages/core/api/routers/simulation.py", "packages.domain.project.service"),
        # skill：技能端点要读项目。
        ("packages/core/api/routers/skill.py", "packages.domain.project.service"),
        # story_state：故事状态端点要读项目。
        ("packages/core/api/routers/story_state.py", "packages.domain.project.service"),
        # volumes：分卷端点复用 domain.volume 的领域服务。
        ("packages/core/api/routers/volumes.py", "packages.domain.volume"),
        # workflows/common：run 详情要读章节。
        ("packages/core/api/routers/workflows/common.py", "packages.domain.chapter.service"),
        # workflows/control：run 控制（启动/暂停/恢复）要读章节 + 项目。
        ("packages/core/api/routers/workflows/control.py", "packages.domain.chapter.service"),
        ("packages/core/api/routers/workflows/control.py", "packages.domain.project.service"),
        # workflows/runs：run 列表/详情要读章节 + 项目 + 分卷（+ 角色/世界的完成度统计）。
        ("packages/core/api/routers/workflows/runs.py", "packages.domain.chapter.service"),
        ("packages/core/api/routers/workflows/runs.py", "packages.domain.project.service"),
        ("packages/core/api/routers/workflows/runs.py", "packages.domain.volume.service"),
        ("packages/core/api/routers/workflows/runs.py", "packages.domain.character.service"),
        ("packages/core/api/routers/workflows/runs.py", "packages.domain.world.service"),
        # worlds：世界实体端点复用 domain.world 的领域服务与出参模型。
        ("packages/core/api/routers/worlds.py", "packages.domain.world"),
        ("packages/core/api/routers/worlds.py", "packages.domain.world.models"),
    }
)

#: ``core → domain`` 方向的禁止前缀（与 ``LAYERING_DIRECTIONS`` 第四行一致）。
_TARGET_PKG_DOMAIN = "packages.domain"

#: **受控例外白名单的索引**：``(扫描根, 禁止前缀)`` → 允许的 ``(文件, 模块)`` 集合。
#:
#: ⚠️ 键**必须带扫描根**：``packages.domain`` 这个禁止前缀在两条方向上各出现一次
#:    （core → domain 与 domain → domain）⇒ 只按前缀索引会把两条方向的例外混在一起。
ALLOWED_STATIC_IMPORTS: dict[tuple[str, str], frozenset[tuple[str, str]]] = {
    ("packages/core", _TARGET_PKG_DOMAIN): ALLOWED_CORE_TO_DOMAIN_IMPORTS,
    ("packages/domain", _TARGET_PKG_DOMAIN): ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS,
}

#: 「字符串里出现了 ``packages.workflows``」的点分路径感知判据。
#: ``packages.workflows.chapter_write`` 命中；``notpackages.workflows`` 不命中。
_WORKFLOWS_TOKEN_RE = re.compile(r"(?<![\w.])packages\.workflows(?![\w])")

#: 「字符串里出现了 ``packages.domain``」的点分路径感知判据（与 workflows 同款）。
_DOMAIN_TOKEN_RE = re.compile(r"(?<![\w.])packages\.domain(?![\w])")

#: ``apps`` 目标的判据：**整串就是模块路径**（``apps`` / ``apps.web.x``）。
#:
#: ⚠️ 为什么 ``apps`` 不能用 workflows 那套「包含」语义：``apps`` 是**普通英文词**，
#:    「包含」语义在当前树上就**实测误报 2 处**
#:    （``packages/core/api/version_check.py`` 的两条提示文案里出现了 ``apps/web/dist``）
#:    ⇒ 纯文案会被当成「动态 import 应用层」⇒ 规则退化成噪声源。
#:    而「整串就是模块路径」既抓得住 ``import_module("apps")`` / ``("apps.web.x")``，
#:    又不会误伤文案。
_APPS_MODULE_PATH_RE = re.compile(r"apps(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

#: **动态 import 目标前缀表**（2026-10-23 task-7 A1）：
#: ``(目标前缀, 判据类型, 正则)``。
#:
#: - ``contains`` = 点分路径感知的「**包含**」（workflows / domain：这两个串足够独特，
#:   「包含」语义既抓得住 ``importlib.import_module("packages" + "." + "domain.x")``
#:   这种拆串形态，又不会误伤普通文案）；
#: - ``module_path`` = **整串**必须是一个模块路径（``apps``：普通英文词 ⇒ 见上）。
#:
#: ⚠️ 这张表是**动态**扫描的目标集合；静态扫描的目标集合 = ``LAYERING_DIRECTIONS``
#:    的「禁止前缀」列 ⇒ 两条扫描线覆盖的目标**同一个来源**，不许各写一份。
_DYNAMIC_TARGET_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (_TARGET_PKG_TOP, "contains", _WORKFLOWS_TOKEN_RE),
    (_TARGET_PKG_DOMAIN, "contains", _DOMAIN_TOKEN_RE),
    ("apps", "module_path", _APPS_MODULE_PATH_RE),
)


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py") if "__pycache__" not in p.parts
    )


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _parse(path: Path) -> ast.AST | None:
    """解析源码 AST；解析失败返回 ``None``（调用方须**显式**处理，不得静默跳过）。

    ⚠️ **必须用 ``utf-8-sig``**（2026-10-23 task-12 X-2 修复）：Python 的 import 走
    ``utf-8-sig``（**容忍 BOM**），而 ``ast.parse(str)`` **不容忍** BOM ⇒ 此前用
    ``utf-8`` 读 ⇒ BOM 字符留在串首 ⇒ ``ast.parse`` 抛 ``SyntaxError`` ⇒ 本函数
    ``except`` 吞掉返回 ``None`` ⇒ **调用方（各扫描点）``continue`` 静默跳过**
    ⇒ 分层看守自己的执行域少了一块。

    实测（本仓真实语料）：``packages/**`` 扫描根共 **227** 个 ``.py``，其中 **3 个**
    带 BOM（``packages/workflows/chapter_write/{polisher,save_draft,scene_planner}.py``）
    ⇒ 旧写法下执行域只有 **224/227**；改 ``utf-8-sig`` 后 **227/227**。

    ⚠️ **校验器必须镜像被测运行时的加载语义**（这里 = Python import 的 BOM 容忍）——
    否则会把一个**合法源文件**误判成「语法错误」，再被 ``except`` 吞成「没看见」。
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


# ---------------------------------------------------------------------------
# 静态 import
# ---------------------------------------------------------------------------


def _static_import_targets(tree: ast.AST, forbidden: str) -> list[tuple[int, str, str]]:
    """静态 import 命中：``(行号, 被导入的模块路径, 描述)``。

    与旧版 ``_static_violations`` 的唯一差别：**多返回「被导入的模块路径」**——
    受控例外白名单的键需要它（粒度 = ``(文件相对路径, 模块路径)``）。

    **外加「import 父包 + 取子包」形态**（2026-10-22 批次二十二修复）：
    ``from packages import workflows`` 会真的执行 ``packages/workflows/__init__.py``
    （该文件存在）⇒ 是货真价实的分层破口；而旧的「只比 ``node.module``」口径
    完全看不见它 ⇒ 静态判据一度形同虚设。
    ⇒ 判据：``from X import Y`` 时，``"X.Y"`` 也可能是一条模块路径 ⇒ 一并检查。
    """
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _matches_prefix(alias.name, forbidden):
                    out.append((node.lineno, alias.name, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if _matches_prefix(module, forbidden):
                out.append((node.lineno, module, f"from {module} import ..."))
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if _matches_prefix(f"{module}.{alias.name}", forbidden):
                    out.append(
                        (
                            node.lineno,
                            f"{module}.{alias.name}",
                            f"from {module} import {alias.name}（经父包取子包 "
                            f"⇒ 等价于 import {module}.{alias.name}）",
                        )
                    )
    return out


def _static_violations(tree: ast.AST, forbidden: str) -> list[tuple[int, str]]:
    """静态 import 命中：``(行号, 描述)``（``_static_import_targets`` 的兼容视图）。"""
    return [(lineno, desc) for lineno, _module, desc in _static_import_targets(tree, forbidden)]


def _is_allowed_core_to_domain(rel_path: str, module: str) -> bool:
    """``core → domain`` 的受控例外判定：**精确**命中 ``(文件, 模块)`` 才允许。

    ⚠️ 口径：这里**只**认「精确登记」这一种放行形式。
    「整个 ``packages/core`` 放行」「按目录放行」都会让
    ``test_core_to_domain_allowance_is_not_coarse`` 红 ⇒ 粗放行在本仓**不可表达**。
    """
    return (rel_path, module) in ALLOWED_CORE_TO_DOMAIN_IMPORTS


def _is_allowed_domain_to_domain(rel_path: str, module: str) -> bool:
    """``domain → domain`` 的受控例外判定：**精确**命中 ``(文件, 模块)`` 才允许。

    ⚠️ 口径与 ``core → domain`` 同款：**只**认精确登记，不许目录级/整层放行。
    """
    return (rel_path, module) in ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS


def _is_allowed_static_import(
    root_rel: str, forbidden: str, rel_path: str, module: str
) -> bool:
    """该 ``(文件, 模块)`` 是否命中**这条方向**的受控例外白名单。

    ⚠️ **必须带扫描根**（``root_rel``）：``packages.domain`` 这个禁止前缀在两条方向上
    各出现一次（core → domain 与 domain → domain）⇒ 只按前缀判会把两条方向的
    例外混在一起（domain→domain 的例外会顺带放过 core→domain）。

    只有 ``core → domain`` 与 ``domain → domain`` 两个方向有受控例外；
    其余方向一律不放行（``core ↛ workflows`` / ``domain ↛ workflows`` / ``packages ↛ apps``）。
    """
    if forbidden == _TARGET_PKG_DOMAIN:
        if root_rel == "packages/core":
            return _is_allowed_core_to_domain(rel_path, module)
        if root_rel == "packages/domain":
            return _is_allowed_domain_to_domain(rel_path, module)
    return False


def scan_static_layering_violations(root_rel: str, forbidden: str, desc: str) -> list[str]:
    """扫 ``root_rel`` 下指向 ``forbidden`` 的**未登记**静态 import（违规列表）。

    「登记」= 命中 ``ALLOWED_STATIC_IMPORTS[(root_rel, forbidden)]`` 受控例外白名单。
    ⚠️ 判据逻辑一行不动，只在**判定是否放行**这一步引入白名单。
    """
    violations: list[str] = []
    for path in _iter_py_files(REPO_ROOT / root_rel):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        for lineno, module, what in _static_import_targets(tree, forbidden):
            if _is_allowed_static_import(root_rel, forbidden, rel, module):
                continue
            violations.append(f"{rel}:{lineno}: {what} ⇒ {desc}")
    return violations


def _is_relative_import(node: ast.AST) -> bool:
    return isinstance(node, ast.ImportFrom) and node.level > 0


def scan_escaping_relative_imports(root_rel: str = "packages/core") -> list[tuple[str, int, str]]:
    """扫 ``root_rel``，返回**逃出本包**的相对 import。

    相对 import 的 ``node.module`` 只是末段名字（``from ...workflows import X`` ⇒
    ``module == "workflows"``、``level == 3``），对静态 import 断言**不可见**。
    对策不是解析绝对目标（需完整包上下文），而是收紧到**无需包上下文即可判定**：
    包内相对导入（``from .common import ...``）照常允许；
    只有 ``level >= 包深度``（能爬出本包）才违规。
    """
    out: list[tuple[str, int, str]] = []
    for path in _iter_py_files(REPO_ROOT / root_rel):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        package_depth = len(path.relative_to(REPO_ROOT).parts) - 1
        for node in ast.walk(tree):
            if _is_relative_import(node) and node.level >= package_depth:
                out.append(
                    (
                        rel,
                        getattr(node, "lineno", 0),
                        f"from {'.' * node.level}{node.module or ''} import ... "
                        f"（level={node.level} >= 包深度 {package_depth}，逃出 {root_rel}）",
                    )
                )
    return sorted(out)


# ---------------------------------------------------------------------------
# 动态 import（常量折叠 + 显式白名单）
# ---------------------------------------------------------------------------


def _fold_str(node: ast.AST, env: dict[str, str | None]) -> str | None:
    """把字符串表达式折叠成字面值；折叠不出来返回 ``None``（=「不可判定」）。

    支持：字符串常量 / 已在 ``env`` 里的名字 / ``a + b`` 拼接 / 纯常量 f-string。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_str(node.left, env)
        right = _fold_str(node.right, env)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None  # 含插值 ⇒ 不可折叠
        return "".join(parts)
    return None


def _module_string_env(tree: ast.AST) -> dict[str, str | None]:
    """收集**全树**（不限作用域）的字符串绑定。

    - 同一名字被赋**多个不同值** ⇒ 记 ``None``（不可判定，防遮蔽误判）。
    - 迭代到不动点 ⇒ 支持 ``A = "packages."`` → ``B = A + "workflows"`` 这类链。
    - **函数内**的局部常量**也算**：``packages/core/api/main.py`` 的
      ``_workflows_pkg`` 就在 ``create_app()`` 里 ⇒ 只收模块级绑定时它折不出来
      ⇒ 白名单命中不了（2026-10-22 批次二十二实测的 critical）。
    """
    exprs: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exprs.setdefault(target.id, []).append(node.value)
    env: dict[str, str | None] = {}
    for _ in range(len(exprs) + 1):
        changed = False
        for name, values in exprs.items():
            folded: set[str] = set()
            ok = True
            for value in values:
                result = _fold_str(value, env)
                if result is None:
                    ok = False
                    break
                folded.add(result)
            new: str | None = folded.pop() if (ok and len(folded) == 1) else None
            if env.get(name, "\0unset") != new:
                env[name] = new
                changed = True
        if not changed:
            break
    return env


def _import_bindings(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """返回 ``(模块别名 → 规范模块名, 可调用别名 → 规范可调用名)``。"""
    modules: dict[str, str] = {}
    callables: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    modules[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                full = f"{node.module}.{alias.name}"
                if alias.asname:
                    callables[alias.asname] = full
                else:
                    callables[alias.name] = full
    return modules, callables


#: 已知「导入 callee」的规范名集合。
IMPORT_CALLEES = frozenset(
    {"importlib.import_module", "importlib.__import__", "__import__", "exec", "eval"}
)


def _resolve_import_callee(
    func: ast.AST,
    modules: dict[str, str],
    callables: dict[str, str],
) -> str | None:
    """把被调用者解析成**规范名**；不是导入 callee 则返回 ``None``。

    覆盖 2026-10-22 批次二十二实测漏掉的四种间接化：

    - ``from importlib import import_module``（⇒ ``callables``）
    - ``import importlib as il``（⇒ ``modules``）
    - ``functools.partial(importlib.import_module)``（⇒ 解 ``partial`` 的第一实参）
    - ``getattr(importlib, "import_module")``（⇒ 解 ``getattr`` 的第二实参）
    """
    if isinstance(func, ast.Name):
        if func.id in callables and callables[func.id] in IMPORT_CALLEES:
            return callables[func.id]
        if func.id in ("__import__", "exec", "eval"):
            return func.id
        return None
    if isinstance(func, ast.Attribute):
        base = func.value
        if (
            isinstance(base, ast.Name)
            and modules.get(base.id) == "importlib"
            and func.attr in ("import_module", "__import__")
        ):
            return f"importlib.{func.attr}"
        return None
    if isinstance(func, ast.Call):
        inner = func.func
        if (
            isinstance(inner, ast.Attribute)
            and inner.attr == "partial"
            and isinstance(inner.value, ast.Name)
            and modules.get(inner.value.id) == "functools"
            and func.args
        ):
            return _resolve_import_callee(func.args[0], modules, callables)
        if (
            isinstance(inner, ast.Name)
            and inner.id == "getattr"
            and len(func.args) == 2
            and isinstance(func.args[1], ast.Constant)
            and isinstance(func.args[1].value, str)
            and func.args[1].value in ("import_module", "__import__")
        ):
            base = func.args[0]
            if isinstance(base, ast.Name) and modules.get(base.id) == "importlib":
                return f"importlib.{func.args[1].value}"
        return None
    return None


def _call_args(call: ast.Call) -> list[ast.AST]:
    """调用的全部实参（位置 + 关键字值）。"""
    args = list(call.args)
    args.extend(kw.value for kw in call.keywords)
    return args


def _dynamic_target_hit(folded: str, prefix: str) -> bool:
    """折叠出的串是否**指向** ``prefix``（动态 import 目标）。

    判据来自 ``_DYNAMIC_TARGET_RULES``（目标前缀表）：
    - ``contains``：点分路径感知的「包含」（workflows / domain）；
    - ``module_path``：**整串**必须是一个模块路径（apps —— 普通英文词，
      「包含」语义实测会误伤 ``packages/core/api/version_check.py`` 的提示文案）。
    """
    for pfx, kind, rx in _DYNAMIC_TARGET_RULES:
        if pfx != prefix:
            continue
        if kind == "contains":
            return bool(rx.search(folded))
        return bool(rx.fullmatch(folded))
    return False


def _is_allowed_dynamic_import(
    root_rel: str, forbidden: str, rel_path: str, folded: str
) -> bool:
    """动态 import 是否被登记放行。

    ① ``ALLOWED_DYNAMIC_IMPORTS``（(文件, 折叠出的目标串) 粒度）；
    ② **静态白名单里登记过的同一条依赖** ⇒ 静态/动态两种形态同放行
       （同一条 (文件, 模块) 依赖，换个写法不算新依赖）。
    """
    if (rel_path, folded) in ALLOWED_DYNAMIC_IMPORTS:
        return True
    return _is_allowed_static_import(root_rel, forbidden, rel_path, folded)


def _directions_for_root(root_rel: str) -> tuple[tuple[str, str], ...]:
    """某个扫描根下的**全部**依赖方向（``(禁止前缀, 描述)``）。

    ⚠️ 单一来源 = ``LAYERING_DIRECTIONS`` ⇒ 静态线与动态线覆盖的目标**不会分叉**。

    ⚠️ **祖先规则**：扫描根 ``packages/core`` 也要吃 ``("packages", "apps")`` 这条
    方向（``packages`` 是 ``packages/core`` 的祖先 ⇒ 该方向覆盖整棵库层）
    ⇒ 否则动态扫描会漏掉 core 里的「动态 import 应用层」。
    """
    return tuple(
        (forbidden, desc)
        for root, forbidden, desc in LAYERING_DIRECTIONS
        if root == root_rel or root_rel.startswith(root + "/")
    )


#: 需要做**动态** import 扫描的根 = ``LAYERING_DIRECTIONS`` 里出现过的全部根。
DYNAMIC_SCANNED_ROOTS: tuple[str, ...] = tuple(
    sorted({root for root, _forbidden, _desc in LAYERING_DIRECTIONS})
)


def scan_dynamic_imports(
    root_rel: str,
    directions: tuple[tuple[str, str], ...],
) -> tuple[list[str], list[str]]:
    """扫 ``root_rel`` 下指向 ``directions`` 各禁止前缀的**动态** import。

    返回 ``(违规列表, 须登记但未登记的不可判定列表)``。

    两条判据：

    ① **规则 R（callee 无关）**：任何 ``ast.Call`` 的任一实参若可常量折叠为
       **指向某个禁止前缀**的字符串 ⇒ 违规（除非该依赖已登记）。
       ⇒ ``getattr`` / 别名 / from-import / partial / ``exec`` **一次堵死**。
       ⚠️ 目标集合来自 ``directions``（= ``LAYERING_DIRECTIONS`` 派生）⇒
       **不再只有 ``packages.workflows`` 一个目标**（2026-10-23 task-7 A1：
       core → domain 的**动态** import 此前完全没设防）。
    ② **不可判定**：仅当被调用者可解析为**已知 import callee** 且实参折不出时
       ⇒ 须登记在 ``NON_FOLDABLE_DYNAMIC_IMPORTS``（**调用级**：(rel, callee)）。
       ⇒ 不会把 ``log.warning(<不可折叠>)`` 这类普通调用误判。
       ⚠️ 这一条与**目标无关**（折不出 ⇒ 无法证明目标不是禁用的）⇒ 一次扫描只报一遍。
    """
    violations: list[str] = []
    unfingerprintable: list[str] = []
    for path in _iter_py_files(REPO_ROOT / root_rel):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        env = _module_string_env(tree)
        modules, callables = _import_bindings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            line = getattr(node, "lineno", 0)
            args = _call_args(node)
            # ① 规则 R
            for arg in args:
                folded = _fold_str(arg, env)
                if folded is None:
                    continue
                for forbidden, desc in directions:
                    if not _dynamic_target_hit(folded, forbidden):
                        continue
                    if not _is_allowed_dynamic_import(root_rel, forbidden, rel, folded):
                        violations.append(
                            f"{rel}:{line}: 调用实参折叠出 {folded!r}（指向 {forbidden}）"
                            f"——{desc}；如属受控例外请登记白名单"
                        )
                    break
                else:
                    continue
                break
            # ② 不可判定（**仅**已知 import callee）
            callee = _resolve_import_callee(node.func, modules, callables)
            if callee is None or not args:
                continue
            if all(_fold_str(arg, env) is None for arg in args):
                if (rel, callee) not in NON_FOLDABLE_DYNAMIC_IMPORTS:
                    unfingerprintable.append(
                        f"{rel}:{line}: {callee} 的实参**不可常量折叠**"
                        "（无法证明目标不是受禁前缀）；"
                        "要么改成可折叠写法，要么登记进 "
                        "NON_FOLDABLE_DYNAMIC_IMPORTS（(rel, callee) 粒度）"
                    )
    return violations, unfingerprintable


def scan_dynamic_workflow_imports(
    root_rel: str = "packages/core",
) -> tuple[list[str], list[str]]:
    """（兼容层）旧口径：只盯 ``packages.workflows`` 一个目标。

    ⚠️ 新代码请用 ``scan_dynamic_imports`` + ``_directions_for_root``
    （**全部**方向一起扫 ⇒ core → domain 的动态 import 也在内）。
    """
    return scan_dynamic_imports(
        root_rel, ((_TARGET_PKG_TOP, "库层禁止依赖业务流程包"),)
    )


# ---------------------------------------------------------------------------
# 用例
#
# ⚠️ 2026-10-24 瘦身：原「① core 静态 import 业务流程包 = 0」已删——被「⑦ 四条方向边」
# **完全蕴含**（方向表第一条即 core↛workflows，静态扫描走同一判据）+ ⑥ 的字面量兜底
# ⇒ 零判别力损失（下方编号**保留原样不重排** ⇒ 不动承重用例的 docstring）。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("root_rel", GUARDED_ROOTS)
def test_no_escaping_relative_imports(root_rel: str) -> None:
    """② core **与 domain** 都禁止逃出本包的相对 import（封住静态断言的盲区）。

    ⚠️ 2026-10-23 task-7 A2：此前**只有** core 根调用了逃逸扫描器
    ⇒ ``domain → workflows`` 可以用相对 import 绕过
    （``packages/domain/character/service.py`` 里写 ``from ...workflows.chapter_write
    import pipeline`` ⇒ level=3 ⇒ 从 ``packages.domain.character`` 上溯 3 级
    ⇒ **真实解析到** ``packages.workflows.chapter_write``），
    而静态判据只看 ``node.level == 0`` ⇒ 对相对 import **不可见**。
    ⇒ 现在**两个根都扫**（``GUARDED_ROOTS``）。
    """
    hits = scan_escaping_relative_imports(root_rel)
    assert not hits, (
        f"{root_rel} 下出现逃出本包的相对 import；命中:\n"
        + "\n".join(f"{rel}:{ln}: {desc}" for rel, ln, desc in hits)
    )


def test_no_dynamic_workflow_import_outside_whitelist() -> None:
    """③ **动态 import 也要守**（**每个方向**各一遍）：拆字符串不再能绕过。

    判别（撤销本修复的写法会让本用例红）：在 ``packages/core/quality/engine.py`` 里加
    ``importlib.import_module("packages" + "." + "workflows")`` ⇒ 折叠出
    ``packages.workflows`` ⇒ 不在白名单 ⇒ 违规。

    ⚠️ 2026-10-23 task-7 A1：扫描目标**不再是**只有 ``packages.workflows``
    ⇒ 目标集合由 ``LAYERING_DIRECTIONS`` 派生（``_directions_for_root``），
    **core → domain 的动态 import 现在也在守**。
    """
    for root_rel in DYNAMIC_SCANNED_ROOTS:
        directions = _directions_for_root(root_rel)
        violations, unfingerprintable = scan_dynamic_imports(root_rel, directions)
        assert not violations, (
            f"{root_rel} 出现指向受禁前缀的动态 import（常量折叠后可判定）；命中:\n"
            + "\n".join(violations)
        )
        assert not unfingerprintable, (
            f"{root_rel} 存在**不可判定**的动态 import —— "
            "请改成可折叠写法或显式登记；命中:\n" + "\n".join(unfingerprintable)
        )


def test_dynamic_import_allowlists_are_pinned() -> None:
    """④ 两个白名单**精确集合相等** ⇒ 换掉一条（条数不变）也必红。

    ⚠️ 旧版只钉条数 ⇒ 把唯一那条**换成另一个文件**（条数仍是 1）测试照绿
    ⇒ 白名单可以被悄悄换掉而不被发现（2026-10-22 批次二十二实测的 minor）。
    """
    assert ALLOWED_DYNAMIC_IMPORTS == frozenset(
        {("packages/core/api/main.py", "packages.workflows")}
    ), (
        "ALLOWED_DYNAMIC_IMPORTS 必须逐条登记（加一条 / 换一条都要改这里）；"
        f"实得 {sorted(ALLOWED_DYNAMIC_IMPORTS)}"
    )
    assert NON_FOLDABLE_DYNAMIC_IMPORTS == frozenset(
        {("packages/core/api/routers/__init__.py", "importlib.import_module")}
    ), (
        "NON_FOLDABLE_DYNAMIC_IMPORTS 必须逐条登记（加一条 / 换一条都要改这里）；"
        f"实得 {sorted(NON_FOLDABLE_DYNAMIC_IMPORTS)}"
    )


def test_entry_main_uses_dynamic_import_for_workflow_discovery() -> None:
    """⑤ 装配入口仍必须用动态 import 触发业务流程注册（V1.5 约定，防被误删）。

    ⚠️ 折叠必须看得见**函数内**的局部常量：``main.py`` 的
    ``_workflows_pkg = "packages" + "." + "workflows"`` 在 ``create_app()`` 内
    ⇒ 只收模块级绑定时它折不出来 ⇒ 本用例会误红（2026-10-22 实测的 critical）。
    """
    tree = _parse(ENTRY_FILE)
    assert tree is not None
    env = _module_string_env(tree)
    modules, callables = _import_bindings(tree)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _resolve_import_callee(node.func, modules, callables)
        if callee not in ("importlib.import_module", "importlib.__import__", "__import__"):
            continue
        for arg in _call_args(node):
            target = _fold_str(arg, env)
            if target is not None and _matches_prefix(target, _TARGET_PKG_TOP):
                found = True
    assert found, (
        "packages/core/api/main.py 必须通过 importlib.import_module(...) "
        "触发业务流程顶层 import（V1.5 架构约定）"
    )


def test_grep_packages_workflows_in_library_layer_is_zero() -> None:
    """⑥ 兜底字面量断言：core 与 domain 下 ``packages.workflows`` 字面值命中数为 0。

    ⚠️ **旧版的 ``main.py`` 豁免已删除**（2026-10-22 实测：``main.py`` 含该字面值
    **0 次** ⇒ 豁免用不上）。旧版「全局禁止字面值」**反而逼**任何动态 import 只能用
    **拆字符串**形态 —— 那正是盲区成因；现在由 ①②③ 精确看守，字面量禁令恢复全局生效。
    """
    hits: list[str] = []
    for root_rel in GUARDED_ROOTS:
        for path in _iter_py_files(REPO_ROOT / root_rel):
            rel = _rel(path)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if _TARGET_PKG_TOP in line:
                    hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, (
        "core/domain 下出现 'packages.workflows' 字面值；命中:\n" + "\n".join(hits)
    )


def test_layering_directions_are_clean() -> None:
    """⑦ 四条方向边都不许破（此前只有 core↛workflows 一个看守）。

    - `packages/core` ↛ `packages/workflows`：core 不得依赖流程组装层；
    - `packages/domain` ↛ `packages/workflows`：领域层不得依赖流程组装层；
    - `packages/**` ↛ `apps`：库层不得依赖应用层；
    - `packages/core` ↛ `packages.domain`：**受控例外**——只有
      `ALLOWED_CORE_TO_DOMAIN_IMPORTS` 里**精确登记**的那些 (文件, 模块) 放行，
      其余一律违规（逐条清单见 task-7 报告）。

    实测基线（2026-10-22）：前三条方向边均为 0；
    第四条（2026-10-23 task-7）：44 条唯一 (文件, 模块) 已登记、0 条未登记。
    """
    hits: list[str] = []
    for root_rel, forbidden, desc in LAYERING_DIRECTIONS:
        hits.extend(scan_static_layering_violations(root_rel, forbidden, desc))
    assert not hits, "分层方向被破坏：\n" + "\n".join(hits)


# ===========================================================================
# 批次二十二（dev-a 独立验证 + 修复）：绕过形态逐条喂判据
# ===========================================================================
# 手法：**合成源码字符串** + ``ast.parse``，**不往仓库注入任何真文件**。
# 对文件系统扫描型判据，替换的只是**两个入口**（「文件来源」与「解析器」），
# **判据逻辑（折叠 / 白名单 / 前缀匹配）一行不动**。

_PROBE_REL = "packages/core/quality/_probe.py"
_PROBE_REL_DOMAIN = "packages/domain/_probe.py"
_PROBE_REL_DEEP = "packages/core/x/y.py"


def _with_synthetic_source(monkeypatch, source: str, rel: str):
    """把合成源码装进本模块的扫描入口（不落盘）。返回 ``(tree, fake_path)``。"""
    tree = ast.parse(source)
    fake_path = REPO_ROOT / rel
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_iter_py_files", lambda root: [fake_path])
    monkeypatch.setattr(module, "_parse", lambda path: tree)
    return tree, fake_path


#: 8 种动态绕过形态 ⇒ 每一种都登记它**应当走哪条通道**被拦：
#: ``violation`` = 规则 R 命中；``unfingerprintable`` = 折不出 ⇒ 须登记。
#: （两种都是「拦」，但通道不同 ⇒ 断言必须分别钉住，否则测的就不是同一个机制。）
_DYNAMIC_BYPASS_FORMS: tuple[tuple[str, str, str], ...] = (
    (
        "getattr-attr",
        'import importlib\n'
        'getattr(importlib, "import_module")("packages" + "." + "workflows")\n',
        "violation",
    ),
    (
        "from-import-alias",
        "from importlib import import_module\n"
        'import_module("packages" + "." + "workflows")\n',
        "violation",
    ),
    (
        "module-alias",
        "import importlib as il\n"
        'il.import_module("packages" + "." + "workflows")\n',
        "violation",
    ),
    (
        "functools-partial",
        "import functools\n"
        "import importlib\n"
        'functools.partial(importlib.import_module)("packages" + "." + "workflows")\n',
        "violation",
    ),
    (
        "dict-subscript",
        'import importlib\n'
        '_PKG = {"wf": "packages.workflows"}\n'
        'importlib.import_module(_PKG["wf"])\n',
        "unfingerprintable",
    ),
    (
        "exec-split",
        'exec("import packages" + ".workflows")\n',
        "violation",
    ),
    (
        "star-args",
        "from importlib import import_module\n"
        'import_module(*["packages" + "." + "workflows"])\n',
        "unfingerprintable",
    ),
    (
        "baseline-split",
        "import importlib\n"
        'importlib.import_module("packages" + "." + "workflows")\n',
        "violation",
    ),
)


@pytest.mark.parametrize(
    ("source", "channel"),
    [(src, ch) for _n, src, ch in _DYNAMIC_BYPASS_FORMS],
    ids=[name for name, _s, _c in _DYNAMIC_BYPASS_FORMS],
)
def test_dynamic_bypass_forms_are_detected(monkeypatch, source, channel):
    """① **8 种动态绕过形态** ⇒ 每一种都必须被拦（通道按登记）。

    这 8 种是 2026-10-22 审查员实测的清单（其中 6 种旧实现漏过）。
    """
    _with_synthetic_source(monkeypatch, source, _PROBE_REL)
    violations, unfingerprintable = scan_dynamic_workflow_imports("packages/core")
    if channel == "violation":
        assert violations, f"漏过（未判违规）：{source!r}"
    else:
        assert unfingerprintable, f"漏过（未判不可判定）：{source!r}"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from packages.workflows.chapter_write import pipeline\n",
            id="static-from",
        ),
        pytest.param(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from packages.workflows.x import Y\n",
            id="static-type-checking",
        ),
        pytest.param(
            "def f():\n    from packages.workflows.x import Y\n",
            id="static-in-function",
        ),
        pytest.param(
            "from packages import workflows\n",
            id="static-parent-package",
        ),
    ],
)
def test_static_bypass_forms_are_detected(source):
    """② 静态四种形态：普通 / ``TYPE_CHECKING`` 块内 / 函数体内 / **经父包取子包**。

    最后一种（``from packages import workflows``）会真的执行
    ``packages/workflows/__init__.py``（该文件存在）⇒ 是货真价实的分层破口。
    """
    tree = ast.parse(source)
    assert _static_violations(tree, _TARGET_PKG_TOP), f"漏过：{source!r}"


@pytest.mark.parametrize(
    ("source", "rel", "root_rel"),
    [
        ("from ...workflows import Y\n", _PROBE_REL_DEEP, "packages/core"),
        (
            "from ...workflows.chapter_write import pipeline",
            "packages/domain/character/service.py",
            "packages/domain",
        ),
    ],
    ids=["core-deep", "domain-root"],
)
def test_relative_escape_is_detected(monkeypatch, source, rel, root_rel):
    """③ 相对逃逸：能爬出本包的相对 import ⇒ 必须被拦（**两个根都扫**）。

    ⚠️ **A2 判别**（``domain`` 根那条）：``packages/domain/character/service.py``
    （包深度 3）里写 ``from ...workflows.chapter_write import pipeline`` ⇒ level=3
    ⇒ 真实解析到 ``packages.workflows.chapter_write`` ⇒ 静态判据只看
    ``node.level == 0`` ⇒ 对相对 import 不可见 ⇒ 只有逃逸扫描器能抓住它。

    ⚠️ **报告内容**也必须可读（不只「有命中」）：点名**源文件** / **level** /
    **目标包**三样 ⇒ 命中了也要能定位。
    """
    _with_synthetic_source(monkeypatch, source, rel)
    hits = scan_escaping_relative_imports(root_rel)
    assert hits, f"漏过：{root_rel} 下的相对逃逸（{source!r}）"
    assert hits[0][0] == rel, f"逃逸报告没点名源文件：{hits[0]}"
    assert "level=3" in hits[0][2], f"逃逸报告里没有 level：{hits[0]}"
    assert "workflows" in hits[0][2], f"逃逸报告里没有点名目标包：{hits[0]}"


def test_domain_root_is_guarded_too(monkeypatch):
    """④ **domain 也被守**（旧实现只扫 core ⇒ 动态方向是「半边看守」）。

    把基准形态放进 ``packages/domain/thing.py`` ⇒ 必须被判违规。
    """
    source = "import importlib\n" 'importlib.import_module("packages" + "." + "workflows")\n'
    _with_synthetic_source(monkeypatch, source, _PROBE_REL_DOMAIN)
    violations, _unfingerprintable = scan_dynamic_workflow_imports("packages/domain")
    assert violations, "漏过：domain 下的动态 import 未被判违规"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            'import importlib\nimportlib.import_module("packages.core.quality")\n',
            id="importlib-core",
        ),
        pytest.param(
            '__import__("json")\n',
            id="dunder-stdlib",
        ),
    ],
)
def test_legitimate_dynamic_forms_are_not_flagged(monkeypatch, source):
    """⑤ 反向用例：库层内部的**合法**动态 import ⇒ 不得误报（否则守看会变成噪声源）。"""
    _with_synthetic_source(monkeypatch, source, _PROBE_REL)
    violations, unfingerprintable = scan_dynamic_workflow_imports("packages/core")
    assert not violations, f"误报（判违规）：{source!r}"
    assert not unfingerprintable, f"误报（判不可判定）：{source!r}"


def test_whitelisted_entry_form_is_not_flagged(monkeypatch):
    """⑥ 反向用例：白名单登记的那一处（``main.py`` 指向 ``packages.workflows``）
    ⇒ 不得判违规。

    喂的是**模块级**常量形态（与 ``ALLOWED_DYNAMIC_IMPORTS`` 的登记语义
    一致：折叠后的目标必须**恰好**是 ``packages.workflows``，精确匹配、不许前缀通配）。
    """
    source = (
        "import importlib\n"
        '_PKG = "packages" + "." + "workflows"\n'
        "importlib.import_module(_PKG)\n"
    )
    _with_synthetic_source(monkeypatch, source, "packages/core/api/main.py")
    violations, unfingerprintable = scan_dynamic_workflow_imports("packages/core")
    assert not violations, f"白名单登记的形态被判违规：{violations}"
    assert not unfingerprintable, f"白名单登记的形态被判不可判定：{unfingerprintable}"


def test_real_entry_main_function_local_binding_is_clean(monkeypatch):
    """⑦ **真实** ``main.py`` 的那条动态 import（**函数内**局部常量形态）⇒ 应当干净。

    ⚠️ 这条是 2026-10-22 批次二十二实测的 **critical**：
    ``_workflows_pkg = "packages" + "." + "workflows"`` 在 ``create_app()`` **函数体内**
    ⇒ 只收模块级绑定时折不出来 ⇒ 既判不出违规、也命中不了白名单。
    ⇒ 折叠器改为**全树**后本用例才绿（⇒ 它是「折叠器作用域」这条判据的判别点）。
    """
    tree = _parse(ENTRY_FILE)
    assert tree is not None
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_iter_py_files", lambda root: [ENTRY_FILE])
    monkeypatch.setattr(module, "_parse", lambda path: tree)
    violations, unfingerprintable = scan_dynamic_workflow_imports("packages/core")
    assert not violations, violations
    assert not unfingerprintable, unfingerprintable


# ===========================================================================
# 批次二十四后（2026-10-23 task-12 X-2）：**看守自己的执行域**可见
# ===========================================================================


def test_every_guarded_python_file_is_actually_parsed() -> None:
    """⑨ 遍历到的每个 ``.py`` 都必须**真被解析**——不许静默跳过。

    ⚠️ **为什么必须钉这条**：``_parse`` 对解析失败返回 ``None``，而各扫描点都是
    ``if tree is None: continue`` ⇒ **解析失败的文件被静默跳过**，分层看守
    **看不见**它们 ⇒ 一条分层破口可以藏在里面而门禁全绿。

    ⇒ 本条把「跳过」变成**红**并点名文件（逐个扫描根 ⇒ 执行域可见）。

    ⚠️ **判别点是真的**：本仓 ``packages/**`` 扫描根内有 **3 个带 BOM 的源文件**，
    用 ``utf-8`` 读它们 ⇒ ``ast.parse`` 抛 ``SyntaxError`` ⇒ 被跳过 ⇒ 本条红；
    改 ``utf-8-sig`` 后 ⇒ 全部解析成功 ⇒ 本条绿。

    ⚠️ 2026-10-24 瘦身：原末尾还有一条 ``assert parsed_all == total_all``
    （执行域计数对账）已删——它被上面那条 ``assert not skipped`` **完全蕴含**
    （skipped 为空 ⇒ 解析到的 == 遍历到的）⇒ 零判别力损失；两个计数器随之成为
    死代码，一并删除。
    """
    for root_rel, _forbidden, _desc in LAYERING_DIRECTIONS:
        files = _iter_py_files(REPO_ROOT / root_rel)
        skipped = [_rel(p) for p in files if _parse(p) is None]
        assert not skipped, (
            f"{root_rel} 下这些 .py **解析失败被静默跳过**（分层看守看不见它们）：{skipped}"
            "（BOM 文件须用 utf-8-sig 读——Python import 容忍 BOM，ast.parse 不容忍）"
        )

    # 扫描根必须覆盖**整个库层**（``packages/``）⇒ 「全仓 .py」在库层内无死角。
    scanned_roots = {root_rel for root_rel, _f, _d in LAYERING_DIRECTIONS}
    assert "packages" in scanned_roots, (
        f"扫描根没覆盖整个库层 packages/（当前 {sorted(scanned_roots)}）"
        "⇒ 「全仓 .py 无死角」这个前提不成立"
    )


def test_parse_handles_a_utf8_bom_file(tmp_path: Path) -> None:
    """⑨′ **合成** BOM 文件 ⇒ ``_parse`` 必须看得见它（不依赖仓内恰好有 BOM 文件）。

    ⚠️ **为什么要合成一条**：上面那条真实数据判定依赖「仓内此刻恰好有带 BOM 的文件」
    ⇒ 哪天有人把那些文件的 BOM 去掉，它就**恒绿**（不再判别）。
    ⇒ 本条用 ``tmp_path`` 造一个带 BOM 的 ``.py`` ⇒ 无论仓内 BOM 状况如何都判别。

    判别：撤掉 ``_parse`` 的 ``utf-8-sig``（退回 ``utf-8``）⇒ 本条必红。
    """
    bom_file = tmp_path / "bom_module.py"
    bom_file.write_bytes(
        b"\xef\xbb\xbf" + b"import os\n\n\ndef f():\n    return os.sep\n"
    )
    assert bom_file.read_bytes().startswith(b"\xef\xbb\xbf"), "夹具前提：BOM 真的写进去了"

    tree = _parse(bom_file)
    assert tree is not None, (
        "带 BOM 的 .py 解析失败 ⇒ _parse 静默跳过它 ⇒ 分层看守看不见这个文件"
        "（须用 utf-8-sig 读，与 Python import 的加载语义一致）"
    )
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "f" for node in ast.walk(tree)
    ), "解析出的 AST 里没有那个函数 ⇒ 解析结果不是这份源码的"


# ===========================================================================
# 2026-10-23 task-7（M-11）：`core → domain` = **受控例外** + 机器看守
# ===========================================================================
#
# Lead 裁决（2026-10-23，勿说过头）：
#   - `core → domain` **不是**「破口」——模块级图无 2 环、无 3 环，
#     且成文规则只禁过「core 不得 import workflows」；
#   - 真实问题是「边界没定义清楚 + 零机器约束」
#     ⇒ 允许 API 路由层调领域服务 + 允许 5 个机制层文件按**精确白名单**调，
#       **但任何新增的 `core → domain` 依赖都必须让本看守变红**。


def _actual_core_to_domain_imports() -> set[tuple[str, str]]:
    """仓内**实际**的 ``core → domain`` 静态导入集合（``(文件, 模块)``）。"""
    out: set[tuple[str, str]] = set()
    for path in _iter_py_files(CORE_ROOT):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        for _lineno, module, _what in _static_import_targets(tree, _TARGET_PKG_DOMAIN):
            out.add((rel, module))
    return out


@pytest.mark.parametrize(
    ("root", "forbidden"),
    [
        pytest.param("packages/core", "packages.domain", id="core-to-domain"),
        pytest.param("packages/domain", "packages.domain", id="domain-to-domain"),
    ],
)
def test_layering_directions_include_domain_to_domain(root: str, forbidden: str) -> None:
    """④ **受控例外的两条方向边必须登记在册**（``core → domain`` / ``domain → domain``）。

    ⚠️ 判别点：方向边**在册**但白名单**没登记** ⇒ 门禁红（受控例外的另一半）。

    ⚠️ **函数名沿用旧名**：T7-M5 突变规格（``tests/mutation_specs/
    t7_character_delete_and_layering.py``）的注释点名本函数名 ⇒ 保留原名可让
    「删掉 domain→domain 这条边 ⇒ 必红」这条指向保持可读；本函数现同时覆盖
    ``core → domain`` 那条边（参数 id = ``core-to-domain``）。
    """
    pairs = {(r, f) for r, f, _desc in LAYERING_DIRECTIONS}
    assert (root, forbidden) in pairs, (
        f"分层方向里没有 {root}↛{forbidden}：{sorted(pairs)}"
    )


def test_core_to_domain_allowlist_matches_actual_imports() -> None:
    """白名单必须**恰好**等于仓内实际的 ``core → domain`` 导入集合。

    - 少登记 ⇒ 门禁红（新增依赖没登记）；
    - 多登记 ⇒ 本用例红（幽灵条目 = 粗放行的后门）。

    ⚠️ 这条让白名单**自维护**：改了导入写法（模块路径变了）必须同步改白名单。
    """
    actual = _actual_core_to_domain_imports()
    assert actual, "仓内应有 core→domain 导入（否则本看守形同虚设）"
    missing = actual - ALLOWED_CORE_TO_DOMAIN_IMPORTS
    assert not missing, (
        f"这些 core→domain 导入未在白名单登记（门禁会红）：{sorted(missing)}"
    )
    ghost = ALLOWED_CORE_TO_DOMAIN_IMPORTS - actual
    assert not ghost, f"白名单里有仓内不存在的条目（幽灵条目）：{sorted(ghost)}"


def test_core_to_domain_allowance_is_not_coarse() -> None:
    """白名单**不许**被放宽成「整个 ``packages/core`` 放行」或「按目录放行」。

    ⚠️ 判别点：下面这些 ``(文件, 模块)`` 组合**都不在**白名单里 ⇒ 必须一律被拒。
    放宽实现（改成 ``rel.startswith("packages/core")`` 之类）⇒ 本用例必红。
    """
    for rel, module in (
        ("packages/core/arc/service.py", "packages.domain.world.service"),
        ("packages/core/quality/service.py", "packages.domain.ledger"),
        ("packages/core/exporter/builder.py", "packages.domain.project.service"),
        ("packages/core/api/routers/chapters.py", "packages.domain.world.service"),
        ("packages/core/api/routers/brand_new.py", "packages.domain.project.service"),
        ("packages/core/brand_new_module.py", "packages.domain.project.service"),
    ):
        assert not _is_allowed_core_to_domain(rel, module), (
            f"{rel} → {module} 不在白名单里却被放行 ⇒ 白名单被放宽了"
        )


def test_core_to_domain_allowlist_entries_are_concrete() -> None:
    """白名单条目必须是**具体文件 + 具体模块**，不许通配 / 目录粒度。"""
    for rel, module in ALLOWED_CORE_TO_DOMAIN_IMPORTS:
        assert rel.startswith("packages/core/"), f"白名单文件不在 core 下：{rel}"
        assert rel.endswith(".py"), f"白名单条目不是具体 .py 文件：{rel}"
        assert not rel.endswith("/"), f"白名单条目是目录粒度：{rel}"
        assert "*" not in rel and "?" not in rel, f"白名单条目含通配符：{rel}"
        assert module.startswith("packages.domain"), f"白名单模块不在 domain 下：{module}"
        assert "*" not in module, f"白名单模块含通配符：{module}"
        assert (REPO_ROOT / rel).exists(), f"白名单指向的文件不存在：{rel}"


def test_new_core_to_domain_dependency_is_rejected(monkeypatch) -> None:
    """**DoD 判别**：在 ``packages/core/arc/service.py`` 里新增一行
    ``from packages.domain.world.service import WorldService`` ⇒ 守卫**必须红**。

    手法：把**合成源码**装进扫描入口（monkeypatch 两个入口）⇒ **不落盘、
    不改任何生产文件**。
    """
    source = "from packages.domain.world.service import WorldService"
    _with_synthetic_source(monkeypatch, source, "packages/core/arc/service.py")
    hits = scan_static_layering_violations(
        "packages/core", _TARGET_PKG_DOMAIN, "core 机制层不得依赖领域层"
    )
    assert hits, "新增的 core→domain 依赖未被拦下（守卫失效）"


def test_new_core_to_domain_dependency_in_a_new_file_is_rejected(monkeypatch) -> None:
    """新文件里的 ``core → domain`` 依赖同样必须红（白名单不是「目录级通行证」）。"""
    source = "from packages.domain.project.service import ProjectService"
    _with_synthetic_source(monkeypatch, source, "packages/core/brand_new/thing.py")
    hits = scan_static_layering_violations(
        "packages/core", _TARGET_PKG_DOMAIN, "core 机制层不得依赖领域层"
    )
    assert hits, "新文件里的 core→domain 依赖未被拦下（守卫失效）"


def test_registered_core_to_domain_import_is_not_flagged(monkeypatch) -> None:
    """反向用例：白名单里**已登记**的那一条 ⇒ 不得误报（否则守看会变成噪声源）。"""
    source = "from packages.domain.project.service import ProjectService"
    _with_synthetic_source(monkeypatch, source, "packages/core/arc/service.py")
    hits = scan_static_layering_violations(
        "packages/core", _TARGET_PKG_DOMAIN, "core 机制层不得依赖领域层"
    )
    assert not hits, f"已登记的导入被判违规：{hits}"


# ===========================================================================
# 2026-10-23 task-7 返工（对抗审查 B）：A1 动态目标泛化 / A2 相对逃逸双根 /
# A6 白名单字面量 pin / A7 domain→domain 方向边
# ===========================================================================


# ---------------------------------------------------------------------------
# A1：动态 import 的目标**不再只有** packages.workflows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "target"),
    [
        pytest.param(
            'import importlib\nimportlib.import_module("packages.domain.world.service")\n',
            "packages.domain",
            id="plain-domain",
        ),
        pytest.param(
            'import importlib\nimportlib.import_module("packages" + "." + "domain.world")\n',
            "packages.domain",
            id="split-domain",
        ),
        pytest.param(
            "import importlib\n"
            "from importlib import import_module\n"
            'import_module("packages.domain.chapter.service")\n',
            "packages.domain",
            id="from-import-domain",
        ),
        pytest.param(
            "import functools\n"
            "import importlib\n"
            'functools.partial(importlib.import_module)("packages.domain.chapter.service")\n',
            "packages.domain",
            id="partial-domain",
        ),
        pytest.param(
            'import importlib\nimportlib.import_module("apps.web.src")\n',
            "apps",
            id="plain-apps",
        ),
        pytest.param(
            'import importlib\nimportlib.import_module("apps")\n',
            "apps",
            id="bare-apps",
        ),
    ],
)
def test_dynamic_import_of_forbidden_target_is_detected(monkeypatch, source, target):
    """**A1 判别**：动态 import 指向受禁前缀 ⇒ 扫描器**必须报出违规**。

    ⚠️ 2026-10-23 task-7 A1：此前动态扫描的目标写死 ``packages.workflows``
    ⇒ ``importlib.import_module("packages.domain.world.service")`` 在 core 下
    **四个扫描器全部放行**（静态 / 动态 / 相对逃逸 / 字面量兜底）⇒
    「任何新增的 core→domain 依赖必须变红」**只对静态 import 成立**。
    ⇒ 现在目标集合由 ``LAYERING_DIRECTIONS`` 派生（``packages.workflows`` +
    ``packages.domain`` + ``apps``）。
    """
    _with_synthetic_source(monkeypatch, source, _PROBE_REL)
    violations, _unfingerprintable = scan_dynamic_imports(
        "packages/core", _directions_for_root("packages/core")
    )
    assert violations, f"漏过（未判违规）：{source!r}（目标 {target}）"
    assert any(target in v for v in violations), (
        f"违规列表里没有点名目标 {target}：{violations}"
    )


def test_dynamic_apps_prose_is_not_flagged(monkeypatch) -> None:
    """**A1 反向用例**：提示文案里出现 ``apps/web/dist`` ⇒ **不得**误报。

    ⚠️ 这就是 ``apps`` 目标**不能**用 workflows 那套「包含」语义的实证
    （仓内真实文案就有这种：``packages/core/api/version_check.py``）。
    """
    source = (
        'log = "cd apps/web && npm run build"\n'
        "print(log)\n"
    )
    _with_synthetic_source(monkeypatch, source, _PROBE_REL)
    violations, _unfingerprintable = scan_dynamic_imports(
        "packages/core", _directions_for_root("packages/core")
    )
    assert not violations, f"误报（判违规）：{violations}"


# ---------------------------------------------------------------------------
# A2：相对 import 逃逸 —— domain 根也要能报出
# ---------------------------------------------------------------------------


# ⚠️ 2026-10-24 瘦身：本节原两条用例已并入/删除——
#   ① 「domain 根下的相对逃逸必须报出」⇒ 并入参数化的
#      ``test_relative_escape_is_detected``（id = ``domain-root``），且**报告内容**
#      三样（点名源文件 / level / 目标包）全部保留；
#   ② 「domain 域**真实**文件里没有逃逸 ⇒ 报 0 条」⇒ 删除——被
#      ``test_no_escaping_relative_imports``（参数化覆盖 ``GUARDED_ROOTS``，含
#      ``packages/domain`` ⇒ 走**真实文件**断言 0 命中）+ ``test_guarded_roots_include_domain``
#      （接线钉）**完全覆盖** ⇒ 零判别力损失。


def test_guarded_roots_include_domain() -> None:
    """**A2 接线钉**：扫描根必须**同时**覆盖 core 与 domain。

    ⚠️ 2026-10-23 task-7 A2：逃逸扫描器此前**只**以 core 为根调用 ⇒
    「domain → workflows 可用相对 import 绕过」而**无人看守**。
    ⚠️ 这条是**接线**判定：合成用例自己喂源码、自己指定根 ⇒ 它们**测不出**
    「domain 根根本没被接进扫描范围」⇒ 必须另有一条把扫描根集合钉住。
    """
    assert "packages/domain" in GUARDED_ROOTS, (
        f"扫描根漏了 packages/domain：{GUARDED_ROOTS} ⇒ domain 层无人看守"
    )
    assert "packages/core" in GUARDED_ROOTS, (
        f"扫描根漏了 packages/core：{GUARDED_ROOTS}"
    )


# ---------------------------------------------------------------------------
# A6：白名单**内容**必须被字面量钉住
# ---------------------------------------------------------------------------


def test_core_to_domain_allowlist_is_pinned() -> None:
    """**A6 判别**：`ALLOWED_CORE_TO_DOMAIN_IMPORTS` 的**内容**逐条钉住。

    ⚠️ 2026-10-23 task-7 A6：此前只有「白名单 == 仓内实际导入集合」这条
    （**只强制声明、不强制许可**）⇒ 白名单内容可被**悄悄换掉**而审查看不见
    （换掉后若那处 import 仍在，集合相等仍成立 ⇒ 门禁照绿）。
    ⇒ 照 ``test_dynamic_import_allowlists_are_pinned`` 的写法加**字面量 pin**。
    """
    assert ALLOWED_CORE_TO_DOMAIN_IMPORTS == frozenset(
        {
            ("packages/core/arc/service.py", "packages.domain.project.service"),
            ("packages/core/delivery/evidence.py", "packages.domain.chapter.draft_resolver"),
            ("packages/core/delivery/evidence.py", "packages.domain.chapter.service"),
            ("packages/core/delivery/evidence.py", "packages.domain.project.service"),
            ("packages/core/exporter/builder.py", "packages.domain.chapter.service"),
            ("packages/core/quality/service.py", "packages.domain.chapter.draft_resolver"),
            ("packages/core/signing_check/service.py", "packages.domain.chapter.service"),
            ("packages/core/signing_check/service.py", "packages.domain.project.service"),
            ("packages/core/api/routers/chapters.py", "packages.domain.chapter.models"),
            ("packages/core/api/routers/chapters.py", "packages.domain.chapter.service"),
            ("packages/core/api/routers/chapters.py", "packages.domain.project.service"),
            ("packages/core/api/routers/characters.py", "packages.domain.character.models"),
            ("packages/core/api/routers/characters.py", "packages.domain.character.service"),
            ("packages/core/api/routers/characters.py", "packages.domain.project.service"),
            ("packages/core/api/routers/continuation.py", "packages.domain.chapter.service"),
            ("packages/core/api/routers/continuation.py", "packages.domain.project.service"),
            ("packages/core/api/routers/export.py", "packages.domain.project.service"),
            ("packages/core/api/routers/genre.py", "packages.domain.project.service"),
            ("packages/core/api/routers/ledger.py", "packages.domain.ledger"),
            ("packages/core/api/routers/plots.py", "packages.domain.plot"),
            ("packages/core/api/routers/plots.py", "packages.domain.plot.models"),
            ("packages/core/api/routers/projects.py", "packages.domain.project.models"),
            ("packages/core/api/routers/projects.py", "packages.domain.project.service"),
            ("packages/core/api/routers/quality.py", "packages.domain.chapter.service"),
            ("packages/core/api/routers/quality.py", "packages.domain.project.service"),
            ("packages/core/api/routers/reference.py", "packages.domain.project.service"),
            ("packages/core/api/routers/reference.py", "packages.domain.reference"),
            ("packages/core/api/routers/reference.py", "packages.domain.reference.models"),
            ("packages/core/api/routers/reveal_policies.py", "packages.domain.knowledge"),
            ("packages/core/api/routers/reveal_policies.py", "packages.domain.knowledge.models"),
            ("packages/core/api/routers/simulation.py", "packages.domain.project.service"),
            ("packages/core/api/routers/skill.py", "packages.domain.project.service"),
            ("packages/core/api/routers/story_state.py", "packages.domain.project.service"),
            ("packages/core/api/routers/volumes.py", "packages.domain.volume"),
            ("packages/core/api/routers/workflows/common.py", "packages.domain.chapter.service"),
            ("packages/core/api/routers/workflows/control.py", "packages.domain.chapter.service"),
            ("packages/core/api/routers/workflows/control.py", "packages.domain.project.service"),
            ("packages/core/api/routers/workflows/runs.py", "packages.domain.chapter.service"),
            ("packages/core/api/routers/workflows/runs.py", "packages.domain.character.service"),
            ("packages/core/api/routers/workflows/runs.py", "packages.domain.project.service"),
            ("packages/core/api/routers/workflows/runs.py", "packages.domain.volume.service"),
            ("packages/core/api/routers/workflows/runs.py", "packages.domain.world.service"),
            ("packages/core/api/routers/worlds.py", "packages.domain.world"),
            ("packages/core/api/routers/worlds.py", "packages.domain.world.models"),
        }
    ), (
        "ALLOWED_CORE_TO_DOMAIN_IMPORTS 必须逐条登记（加一条 / 换一条都要改这里）；"
        f"实得 {sorted(ALLOWED_CORE_TO_DOMAIN_IMPORTS)}"
    )


# ---------------------------------------------------------------------------
# A7：domain → domain 方向边 + 精确白名单
# ---------------------------------------------------------------------------


def _actual_domain_to_domain_imports() -> set[tuple[str, str]]:
    """仓内**实际**的 ``domain → domain`` 绝对 import 集合（``(文件, 模块)``）。"""
    out: set[tuple[str, str]] = set()
    for path in _iter_py_files(DOMAIN_ROOT):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        for _lineno, module, _what in _static_import_targets(tree, _TARGET_PKG_DOMAIN):
            out.add((rel, module))
    return out


# ⚠️ 2026-10-24 瘦身：「domain → domain 必须登记成一条方向边」已并入上方
# ``test_layering_directions_include_domain_to_domain``（参数 id = ``domain-to-domain``）
# ⇒ 与 core→domain 那条边共用一个参数化函数 ⇒ T7-M5 仍能命中（删掉这条边 ⇒ 该参数变红）。


def test_domain_to_domain_allowlist_matches_actual_imports() -> None:
    """``domain → domain`` 白名单必须**恰好**等于仓内实际导入集合。"""
    actual = _actual_domain_to_domain_imports()
    assert actual, "仓内应有 domain→domain 导入（否则这条方向边形同虚设）"
    missing = actual - ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS
    assert not missing, f"这些 domain→domain 导入未登记：{sorted(missing)}"
    ghost = ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS - actual
    assert not ghost, f"白名单里有仓内不存在的条目（幽灵条目）：{sorted(ghost)}"


def test_domain_to_domain_allowlist_is_pinned() -> None:
    """**A7**：``domain → domain`` 白名单的内容**逐条钉住**（A6 同款要求）。"""
    assert ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS == frozenset(
        {
            ("packages/domain/character/service.py", "packages.domain.world.service"),
        }
    ), (
        "ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS 必须逐条登记（加一条 / 换一条都要改这里）；"
        f"实得 {sorted(ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS)}"
    )


def test_domain_to_domain_allowance_is_not_coarse() -> None:
    """``domain → domain`` 白名单**不许**被放宽成「整个 packages/domain 放行」。"""
    for rel, module in (
        ("packages/domain/character/service.py", "packages.domain.plot.service"),
        ("packages/domain/world/service.py", "packages.domain.character.service"),
        ("packages/domain/chapter/service.py", "packages.domain.world.service"),
        ("packages/domain/brand_new/module.py", "packages.domain.world.service"),
    ):
        assert not _is_allowed_domain_to_domain(rel, module), (
            f"{rel} → {module} 不在白名单里却被放行 ⇒ 白名单被放宽了"
        )


def test_new_domain_to_domain_dependency_is_rejected(monkeypatch) -> None:
    """**A7 判别**：新增一条 domain→domain 依赖 ⇒ 守卫**必须红**。"""
    source = "from packages.domain.chapter.service import ChapterService"
    _with_synthetic_source(monkeypatch, source, "packages/domain/character/service.py")
    hits = scan_static_layering_violations(
        "packages/domain", _TARGET_PKG_DOMAIN, "domain 域之间原则上不得互相依赖"
    )
    assert hits, "新增的 domain→domain 依赖未被拦下（守卫失效）"


def test_registered_domain_to_domain_import_is_not_flagged(monkeypatch) -> None:
    """反向用例：已登记的那一条 ⇒ 不得误报。"""
    source = "from packages.domain.world.service import ReferencedError"
    _with_synthetic_source(monkeypatch, source, "packages/domain/character/service.py")
    hits = scan_static_layering_violations(
        "packages/domain", _TARGET_PKG_DOMAIN, "domain 域之间原则上不得互相依赖"
    )
    assert not hits, f"已登记的导入被判违规：{hits}"


