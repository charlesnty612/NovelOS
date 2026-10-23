#!/usr/bin/env python3
"""Agent Skill 包契约自检（stdlib-only）。

检查项（每条都有可触发的失败信息）
--------------------------------
1. **三平台清单存在**，且各自**只含该平台认识的字段**
   （Cursor 的 ``additionalProperties:false`` 真的实现——未知键直接判红）。
2. **``SKILL.md`` frontmatter 只含 Agent Skills 规范的 6 个字段**：
   ``name`` / ``description`` / ``license`` / ``compatibility`` / ``metadata`` /
   ``allowed-tools``；``name`` 等于父目录名且符合命名规则；
   ``description`` ≤ 1024 字符；**顶层不得有 ``version``**。
3. ``metadata.version`` 与三个平台 ``plugin.json`` 的 ``version`` **逐字一致**。
4. ``SKILL.md`` 里引用的**每个** ``references/*.md`` / ``scripts/*.py`` 都真实存在；
   并**反查**：``references/`` 与 ``scripts/`` 里没有**孤儿文件**（未被 SKILL.md 引用）。
5. ``scripts/novelos.py --help`` 能在**离线**状态退出码 0
   （用 ``sitecustomize.py`` 把 ``socket`` 打掉 ⇒ 真连网就会炸）。
6. **应用版本六处一致**：``pyproject.toml`` 是**单一源**，其余五处
   （``.claude-plugin/plugin.json`` 的 ``metadata.novelosVersion`` /
   ``skills/novelos/SKILL.md`` 的 ``metadata.novelosVersion`` /
   ``apps/web/package.json`` / ``apps/web/package-lock.json`` / ``uv.lock``）
   必须与它**逐字一致**——任一处漏同步即报红。
7. ``--selftest``：造一份**坏副本**，断言本脚本对它**报红**（并断言原树报绿）。

用法::

    python check_skill_package.py              # 正常自检
    python check_skill_package.py --selftest   # 自检 + 变异验证
    python check_skill_package.py --skill-dir <路径> --repo-root <路径>

退出码：``0`` 全绿；``1`` 有检查项未过；``2`` 用法/环境错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# ⚠️ 本机 Windows stdout 默认 cp936（AGENTS 坑区「Windows 默认 cp936 吞中文」家族）。
# 本脚本的输出全是中文诊断 ⇒ 不重配编码时，**在 GBK 控制台上全是乱码**，
# 复核者根本读不出「哪条检查没过」。**两侧都要钉**：本进程 stdout 重配 +
# 子进程 env（下面 subprocess 调用处已带 PYTHONIOENCODING/PYTHONUTF8）。
try:  # pragma: no cover —— 极老解释器上 reconfigure 不存在时静默跳过
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:  # pragma: no cover
    pass

# ---------------------------------------------------------------------------
# 规范常量
# ---------------------------------------------------------------------------

#: Agent Skills 规范允许的**全部** frontmatter 字段（多一个都硬失败）。
SPEC_FRONTMATTER_FIELDS = (
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
)

#: Claude Code 扩展字段（**不属于**规范）——出现在 frontmatter 里必须报红。
CLAUDE_CODE_ONLY_FRONTMATTER_FIELDS = (
    "version",
    "argument-hint",
    "arguments",
    "when_to_use",
    "context",
    "agent",
    "model",
    "effort",
    "hooks",
    "paths",
    "shell",
    "disable-model-invocation",
    "user-invocable",
)

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DESC_MAX = 1024
COMPAT_MAX = 500
NAME_MAX = 64

#: 各平台 ``plugin.json`` 认识的字段集（**互不相同**）。
#:
#: - Claude Code：官方 manifest reference 的 Fields 表全量（含 ``metadata``）。
#: - Cursor：官方 schema 的根属性全量（``additionalProperties:false`` ⇒ 未知键被拒），
#:   **没有** ``metadata``。
#: - Codex：取自 ``openai/plugins`` 官方仓库 ``.codex-plugin/plugin.json`` 实际原文的
#:   字段集 + 同族兄弟插件共用的一组常见字段。Codex 未公布严格 schema，
#:   故此表取**保守可证**的并集（见 README「未验证」一节）。
CLAUDE_PLUGIN_FIELDS = frozenset(
    {
        "$schema", "name", "displayName", "version", "description", "author",
        "homepage", "repository", "license", "keywords", "metadata", "icon",
        "documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl",
        "defaultEnabled", "dependencies", "settings", "userConfig", "types",
        "channels", "skills", "commands", "agents", "hooks", "mcpServers",
        "lspServers", "outputStyles", "workflows", "experimental",
    }
)

CLAUDE_MARKETPLACE_FIELDS = frozenset(
    {
        "$schema", "name", "owner", "plugins", "description", "version", "metadata",
        "forceRemoveDeletedPlugins", "allowCrossMarketplaceDependenciesOn", "renames",
    }
)

#: marketplace 条目字段 = plugin.json 字段 + 条目自有字段 −「目录展示字段」
#: （官方说明：``icon`` / ``documentationUrl`` / ``supportUrl`` /
#: ``privacyPolicyUrl`` / ``termsOfServiceUrl`` 在条目里算未知字段）。
CLAUDE_ENTRY_FIELDS = (
    CLAUDE_PLUGIN_FIELDS
    - {"icon", "documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl"}
) | {"source", "category", "tags", "strict", "relevance", "headers", "headersHelper"}

CURSOR_PLUGIN_FIELDS = frozenset(
    {
        "$schema", "name", "displayName", "description", "version", "minClientVersions",
        "author", "publisher", "homepage", "repository", "license", "logo", "keywords",
        "category", "tags", "commands", "agents", "skills", "rules", "hooks",
        "variables", "mcpServers",
    }
)

#: Cursor 的 ``author`` 是严格对象：**只允许 name / email**（没有 url）。
CURSOR_AUTHOR_FIELDS = frozenset({"name", "email"})

CURSOR_MARKETPLACE_FIELDS = frozenset({"$schema", "name", "owner", "metadata", "plugins"})

CURSOR_ENTRY_FIELDS = frozenset({"name", "source", "description", "minClientVersions"})

CODEX_PLUGIN_FIELDS = frozenset(
    {
        "$schema", "name", "displayName", "version", "license", "author", "description",
        "homepage", "websiteURL", "repository", "keywords", "skills", "mcpServers",
        "apps", "interface", "privacyPolicyURL", "termsOfServiceURL", "composerIcon",
        "logo", "defaultPrompt", "brandColor", "screenshots", "publisher", "category",
        "tags", "agents", "commands", "hooks",
    }
)

CODEX_MARKETPLACE_FIELDS = frozenset(
    {"$schema", "name", "interface", "plugins", "description", "owner", "version", "metadata"}
)

CODEX_ENTRY_FIELDS = frozenset(
    {"name", "source", "policy", "category", "interface", "description", "version"}
)

#: 三平台 ``plugin.json`` 相对仓库根的路径。
PLUGIN_MANIFESTS = {
    "claude": Path(".claude-plugin") / "plugin.json",
    "codex": Path(".codex-plugin") / "plugin.json",
    "cursor": Path(".cursor-plugin") / "plugin.json",
}

MARKETPLACE_MANIFESTS = {
    "claude": Path(".claude-plugin") / "marketplace.json",
    "codex": Path(".agents") / "plugins" / "marketplace.json",
    "cursor": Path(".cursor-plugin") / "marketplace.json",
}

#: 各平台 marketplace 已知字段集。
MARKETPLACE_FIELDS = {
    "claude": CLAUDE_MARKETPLACE_FIELDS,
    "codex": CODEX_MARKETPLACE_FIELDS,
    "cursor": CURSOR_MARKETPLACE_FIELDS,
}

#: 各平台 marketplace **条目**已知字段集。
ENTRY_FIELDS = {
    "claude": CLAUDE_ENTRY_FIELDS,
    "codex": CODEX_ENTRY_FIELDS,
    "cursor": CURSOR_ENTRY_FIELDS,
}

#: 被 skill 引用的资源形态。
REF_MD_RE = re.compile(r"references/[A-Za-z0-9_.\-]+\.md")
REF_PY_RE = re.compile(r"scripts/[A-Za-z0-9_.\-]+\.py")

#: 离线验证用：把 socket 打掉的 sitecustomize。
_BLOCK_SOCKET_SRC = '''\
"""check_skill_package.py 注入：禁止任何网络连接。"""
import socket


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise OSError("network access disabled by check_skill_package.py")


def _blocked(*args, **kwargs):
    raise OSError("network access disabled by check_skill_package.py")


socket.socket = _BlockedSocket
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
'''


# ---------------------------------------------------------------------------
# 极简 YAML 子集解析（frontmatter 专用）
# ---------------------------------------------------------------------------


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value in ("true", "false"):
        return value == "true"
    if value in ("null", "~", ""):
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _split_items(lines: list[str]) -> list[tuple[int, str]]:
    items: list[tuple[int, str]] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        items.append((indent, line.strip()))
    return items


def _parse_block(items: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    result: dict[str, Any] = {}
    while index < len(items):
        item_indent, content = items[index]
        if item_indent < indent:
            break
        if item_indent > indent:
            raise ValueError(f"缩进不一致（期望 {indent} 空格，实得 {item_indent}）：{content!r}")
        key, sep, rest = content.partition(":")
        if not sep:
            raise ValueError(f"不是键值对：{content!r}")
        key = key.strip()
        rest = rest.strip()
        if rest:
            result[key] = _parse_scalar(rest)
            index += 1
            continue
        if index + 1 < len(items) and items[index + 1][0] > indent:
            if items[index + 1][1].startswith("- "):
                seq: list[Any] = []
                while index + 1 < len(items) and items[index + 1][1].startswith("- "):
                    seq.append(_parse_scalar(items[index + 1][1][2:]))
                    index += 1
                result[key] = seq
            else:
                value, index = _parse_block(items, index + 1, items[index + 1][0])
                result[key] = value
        else:
            result[key] = None
            index += 1
    return result, index


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 SKILL.md 的 YAML frontmatter（**受限子集**）。

    支持：顶层键值对、一层（及多层）嵌套映射、``- item`` 序列、引号标量。
    **不支持**：行内注释、锚点、多文档、流式写法——本仓 frontmatter 刻意只用
    上面的子集（保持「人能手读、机器能解析」）。

    返回 ``(mapping, body)``；解析失败抛 ``ValueError``。
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md 未以 '---' 开头的 YAML frontmatter 起始")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError("YAML frontmatter 未闭合（缺第二个 '---'）")
    items = _split_items(lines[1:end])
    if not items:
        return {}, "\n".join(lines[end + 1 :])
    mapping, _ = _parse_block(items, 0, items[0][0])
    return mapping, "\n".join(lines[end + 1 :])


# ---------------------------------------------------------------------------
# 检查结果
# ---------------------------------------------------------------------------


class Check:
    """一条检查的结果。"""

    def __init__(self, check_id: str, ok: bool, message: str) -> None:
        self.check_id = check_id
        self.ok = ok
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.check_id, "ok": self.ok, "message": self.message}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 各项检查
# ---------------------------------------------------------------------------


def check_manifests_exist(repo_root: Path) -> list[Check]:
    checks: list[Check] = []
    for platform, rel in PLUGIN_MANIFESTS.items():
        path = repo_root / rel
        checks.append(
            Check(
                f"manifest-exists/{platform}",
                path.is_file(),
                f"{rel} 存在" if path.is_file() else f"缺少平台清单：{rel}（应存在）",
            )
        )
    for platform, rel in MARKETPLACE_MANIFESTS.items():
        path = repo_root / rel
        checks.append(
            Check(
                f"marketplace-exists/{platform}",
                path.is_file(),
                f"{rel} 存在" if path.is_file() else f"缺少市场清单：{rel}（应存在）",
            )
        )
    return checks


def check_manifest_fields(repo_root: Path) -> list[Check]:
    """每个平台清单**只含该平台认识的字段**。"""
    checks: list[Check] = []
    known = {
        "claude": (PLUGIN_MANIFESTS["claude"], CLAUDE_PLUGIN_FIELDS),
        "codex": (PLUGIN_MANIFESTS["codex"], CODEX_PLUGIN_FIELDS),
        "cursor": (PLUGIN_MANIFESTS["cursor"], CURSOR_PLUGIN_FIELDS),
    }
    for platform, (rel, allowed) in known.items():
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(Check(f"manifest-json/{platform}", False, f"{rel} 不是合法 JSON：{exc}"))
            continue
        if not isinstance(data, dict):
            checks.append(Check(f"manifest-json/{platform}", False, f"{rel} 顶层必须是对象"))
            continue
        unknown = sorted(set(data) - allowed)
        checks.append(
            Check(
                f"manifest-fields/{platform}",
                not unknown,
                (
                    f"{rel} 字段集合规（{len(data)} 个字段，全部为 {platform} 认识的字段）"
                    if not unknown
                    else f"{rel} 含 {platform} **不认识**的字段：{unknown}；"
                    f"该平台只接受 {sorted(allowed)}"
                ),
            )
        )
    # Cursor：author 是严格对象（只允许 name / email）
    cursor_path = repo_root / PLUGIN_MANIFESTS["cursor"]
    if cursor_path.is_file():
        try:
            data = _load_json(cursor_path)
        except (OSError, json.JSONDecodeError):
            data = {}
        author = data.get("author") if isinstance(data, dict) else None
        if isinstance(author, dict):
            extra = sorted(set(author) - CURSOR_AUTHOR_FIELDS)
            checks.append(
                Check(
                    "manifest-fields/cursor-author",
                    not extra,
                    (
                        "cursor plugin.json 的 author 只含 name/email"
                        if not extra
                        else f"cursor plugin.json 的 author 含未知键 {extra}（Cursor 严格拒绝）"
                    ),
                )
            )
    return checks


def check_marketplace_shapes(repo_root: Path) -> list[Check]:
    """三平台 marketplace 的**结构**与 ``source`` 形状（互不兼容，逐个断言）。"""
    checks: list[Check] = []

    # --- Claude Code：source 必须是相对路径字符串且以 "./" 开头 ---
    rel = MARKETPLACE_MANIFESTS["claude"]
    path = repo_root / rel
    if path.is_file():
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(Check("marketplace-json/claude", False, f"{rel} 不是合法 JSON：{exc}"))
            data = {}
        if isinstance(data, dict):
            unknown = sorted(set(data) - MARKETPLACE_FIELDS["claude"])
            checks.append(
                Check(
                    "marketplace-fields/claude",
                    not unknown,
                    (
                        f"{rel} 顶层字段合规"
                        if not unknown
                        else f"{rel} 含未知顶层字段：{unknown}"
                    ),
                )
            )
            plugins = data.get("plugins")
            entries = plugins if isinstance(plugins, list) else []
            checks.append(
                Check(
                    "marketplace-plugins/claude",
                    bool(entries),
                    f"{rel} plugins[] 非空（{len(entries)} 项）" if entries else f"{rel} plugins[] 为空",
                )
            )
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                extra = sorted(set(entry) - ENTRY_FIELDS["claude"])
                checks.append(
                    Check(
                        f"marketplace-entry-fields/claude[{index}]",
                        not extra,
                        (
                            f"claude marketplace entries[{index}] 字段合规"
                            if not extra
                            else f"claude marketplace entries[{index}] 含未知字段：{extra}"
                        ),
                    )
                )
                source = entry.get("source")
                ok = isinstance(source, str) and source.startswith("./")
                checks.append(
                    Check(
                        f"marketplace-source/claude[{index}]",
                        ok,
                        (
                            f"claude marketplace entries[{index}].source = {source!r}"
                            "（相对路径字符串且以 './' 开头）"
                            if ok
                            else f"claude marketplace entries[{index}].source = {source!r} 形状不对："
                            "Claude Code 的相对路径 source 必须是字符串且以 './' 开头"
                        ),
                    )
                )

    # --- Codex：source 必须是 {"source": ..., "path"|"url": ...} 对象 ---
    rel = MARKETPLACE_MANIFESTS["codex"]
    path = repo_root / rel
    if path.is_file():
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(Check("marketplace-json/codex", False, f"{rel} 不是合法 JSON：{exc}"))
            data = {}
        if isinstance(data, dict):
            unknown = sorted(set(data) - MARKETPLACE_FIELDS["codex"])
            checks.append(
                Check(
                    "marketplace-fields/codex",
                    not unknown,
                    (
                        f"{rel} 顶层字段合规"
                        if not unknown
                        else f"{rel} 含未知顶层字段：{unknown}"
                    ),
                )
            )
            entries = data.get("plugins")
            entries = entries if isinstance(entries, list) else []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                extra = sorted(set(entry) - ENTRY_FIELDS["codex"])
                checks.append(
                    Check(
                        f"marketplace-entry-fields/codex[{index}]",
                        not extra,
                        (
                            f"codex marketplace entries[{index}] 字段合规"
                            if not extra
                            else f"codex marketplace entries[{index}] 含未知字段：{extra}"
                        ),
                    )
                )
                source = entry.get("source")
                ok = (
                    isinstance(source, dict)
                    and isinstance(source.get("source"), str)
                    and source["source"] in ("local", "url", "git-subdir", "npm", "github")
                )
                checks.append(
                    Check(
                        f"marketplace-source/codex[{index}]",
                        ok,
                        (
                            f"codex marketplace entries[{index}].source = {json.dumps(source, ensure_ascii=False)}"
                            "（对象形状，含 source 类型键）"
                            if ok
                            else f"codex marketplace entries[{index}].source = {source!r} 形状不对："
                            "Codex 的 source 必须是对象（形如 {\"source\":\"local\",\"path\":\"…\"}）"
                        ),
                    )
                )

    # --- Cursor：source 必须是裸字符串，且**不带 './' 前缀** ---
    rel = MARKETPLACE_MANIFESTS["cursor"]
    path = repo_root / rel
    if path.is_file():
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(Check("marketplace-json/cursor", False, f"{rel} 不是合法 JSON：{exc}"))
            data = {}
        if isinstance(data, dict):
            unknown = sorted(set(data) - MARKETPLACE_FIELDS["cursor"])
            checks.append(
                Check(
                    "marketplace-fields/cursor",
                    not unknown,
                    (
                        f"{rel} 顶层字段合规"
                        if not unknown
                        else f"{rel} 含未知顶层字段：{unknown}（Cursor 严格拒绝）"
                    ),
                )
            )
            entries = data.get("plugins")
            entries = entries if isinstance(entries, list) else []
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                extra = sorted(set(entry) - ENTRY_FIELDS["cursor"])
                checks.append(
                    Check(
                        f"marketplace-entry-fields/cursor[{index}]",
                        not extra,
                        (
                            f"cursor marketplace entries[{index}] 字段合规"
                            if not extra
                            else f"cursor marketplace entries[{index}] 含未知字段：{extra}"
                        ),
                    )
                )
                source = entry.get("source")
                ok = isinstance(source, str) and not source.startswith("./")
                checks.append(
                    Check(
                        f"marketplace-source/cursor[{index}]",
                        ok,
                        (
                            f"cursor marketplace entries[{index}].source = {source!r}"
                            "（裸字符串，不带 './' 前缀）"
                            if ok
                            else f"cursor marketplace entries[{index}].source = {source!r} 形状不对："
                            "Cursor 的 source 是裸字符串，且不应带 './' 前缀"
                        ),
                    )
                )
    return checks


def check_frontmatter(skill_dir: Path) -> list[Check]:
    checks: list[Check] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [Check("skill-md-exists", False, f"缺少 {skill_md}（skill 包必须有 SKILL.md）")]
    checks.append(Check("skill-md-exists", True, f"{skill_md.name} 存在"))
    text = skill_md.read_text(encoding="utf-8")
    try:
        front, body = parse_frontmatter(text)
    except ValueError as exc:
        return checks + [Check("frontmatter-parse", False, f"frontmatter 解析失败：{exc}")]

    keys = set(front)
    unknown = sorted(keys - set(SPEC_FRONTMATTER_FIELDS))
    checks.append(
        Check(
            "frontmatter-fields",
            not unknown,
            (
                f"frontmatter 只含规范 6 字段：{sorted(keys)}"
                if not unknown
                else f"frontmatter 含规范外字段：{unknown}；"
                f"规范只允许 {sorted(SPEC_FRONTMATTER_FIELDS)}（顶层 version 属硬失败写法）"
            ),
        )
    )
    for field in CLAUDE_CODE_ONLY_FRONTMATTER_FIELDS:
        if field in keys:
            checks.append(
                Check(
                    f"frontmatter-no-{field}",
                    False,
                    f"frontmatter 顶层出现 {field!r}——这是 Claude Code 扩展字段，"
                    "不属于 Agent Skills 规范（打包/上传会硬失败）",
                )
            )
    checks.append(
        Check(
            "frontmatter-no-top-level-version",
            "version" not in front,
            "顶层无 version 键（版本放在 metadata.version）"
            if "version" not in front
            else "顶层出现 version 键——规范违规（应放 metadata.version）",
        )
    )

    name = front.get("name")
    expected_name = skill_dir.name
    name_ok = isinstance(name, str) and bool(NAME_RE.match(name)) and len(name) <= NAME_MAX
    checks.append(
        Check(
            "frontmatter-name-format",
            name_ok,
            (
                f"name={name!r} 合规（小写字母数字与连字符、无首尾/连续连字符、≤{NAME_MAX} 字符）"
                if name_ok
                else f"name={name!r} 不合规：必须匹配 {NAME_RE.pattern} 且长度 ≤ {NAME_MAX}"
            ),
        )
    )
    checks.append(
        Check(
            "frontmatter-name-matches-dir",
            name == expected_name,
            (
                f"name={name!r} == 父目录名 {expected_name!r}"
                if name == expected_name
                else f"name={name!r} != 父目录名 {expected_name!r}（规范要求一致）"
            ),
        )
    )

    description = front.get("description")
    desc_ok = isinstance(description, str) and 1 <= len(description) <= DESC_MAX
    checks.append(
        Check(
            "frontmatter-description-length",
            desc_ok,
            (
                f"description 长度 {len(description)} ≤ {DESC_MAX}"
                if desc_ok
                else f"description 长度 {len(description) if isinstance(description, str) else 'N/A'}"
                f" 非法（须 1..{DESC_MAX} 字符）"
            ),
        )
    )

    compatibility = front.get("compatibility")
    if isinstance(compatibility, str):
        checks.append(
            Check(
                "frontmatter-compatibility-length",
                len(compatibility) <= COMPAT_MAX,
                (
                    f"compatibility 长度 {len(compatibility)} ≤ {COMPAT_MAX}"
                    if len(compatibility) <= COMPAT_MAX
                    else f"compatibility 长度 {len(compatibility)} > {COMPAT_MAX}"
                ),
            )
        )

    metadata = front.get("metadata")
    checks.append(
        Check(
            "frontmatter-metadata-mapping",
            isinstance(metadata, dict),
            "metadata 是映射" if isinstance(metadata, dict) else f"metadata 必须是映射，实得 {type(metadata).__name__}",
        )
    )
    checks.append(
        Check(
            "frontmatter-metadata-version",
            isinstance(metadata, dict) and isinstance(metadata.get("version"), str),
            (
                f"metadata.version={metadata.get('version')!r} 是字符串"
                if isinstance(metadata, dict) and isinstance(metadata.get("version"), str)
                else "metadata.version 缺失或不是字符串"
            ),
        )
    )
    if isinstance(metadata, dict):
        checks.append(
            Check(
                "frontmatter-metadata-source",
                isinstance(metadata.get("source"), str) and bool(metadata.get("source")),
                (
                    f"metadata.source={metadata.get('source')!r}"
                    if isinstance(metadata.get("source"), str) and metadata.get("source")
                    else "metadata.source 缺失或非字符串（应填仓库 URL）"
                ),
            )
        )
    checks.append(
        Check(
            "frontmatter-body-nonempty",
            bool(body.strip()),
            f"SKILL.md 正文非空（{len(body.strip())} 字符）" if body.strip() else "SKILL.md 正文为空",
        )
    )
    return checks


def check_version_consistency(skill_dir: Path, repo_root: Path) -> list[Check]:
    """``metadata.version`` 与三个平台清单的 ``version`` **逐字一致**。"""
    checks: list[Check] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [Check("version-consistency/skill", False, "缺少 SKILL.md，无法比对版本")]
    try:
        front, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [Check("version-consistency/skill", False, f"frontmatter 解析失败：{exc}")]
    metadata = front.get("metadata")
    skill_version = metadata.get("version") if isinstance(metadata, dict) else None
    checks.append(
        Check(
            "version-consistency/skill",
            isinstance(skill_version, str) and bool(skill_version),
            f"SKILL.md metadata.version = {skill_version!r}"
            if isinstance(skill_version, str)
            else f"SKILL.md metadata.version 缺失或非字符串：{skill_version!r}",
        )
    )
    for platform, rel in PLUGIN_MANIFESTS.items():
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(Check(f"version-consistency/{platform}", False, f"{rel} 读取失败：{exc}"))
            continue
        manifest_version = data.get("version") if isinstance(data, dict) else None
        ok = isinstance(manifest_version, str) and manifest_version == skill_version
        checks.append(
            Check(
                f"version-consistency/{platform}",
                ok,
                (
                    f"{rel} version={manifest_version!r} 与 metadata.version 逐字一致"
                    if ok
                    else f"{rel} version={manifest_version!r} ≠ metadata.version={skill_version!r}"
                ),
            )
        )
    return checks


_PROJECT_VERSION_RE = re.compile(r'^\s*version\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def _read_pyproject_version(repo_root: Path) -> str | None:
    """读仓库 ``pyproject.toml`` 的 ``[project].version``（stdlib-only，Python 3.9 兼容）。

    不用 ``tomllib``：本脚本声明「Python 3.9+ 标准库」，而 ``tomllib`` 是 3.11 才进标准库。
    这里只需要一个扁平键，改为「先切 ``[project]`` 段、再正则取 version」，顺带避免误命中
    ``[tool.*]`` 段里的同名字段。
    """
    path = repo_root / "pyproject.toml"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^\[project\]\s*$(.*?)(?=^\[)", text, re.MULTILINE | re.DOTALL)
    segment = m.group(1) if m else text
    vm = _PROJECT_VERSION_RE.search(segment)
    return vm.group(1) if vm else None


# ---------------------------------------------------------------------------
# web 三处版本副本（发版纪律「六处」里的后三处）
# ---------------------------------------------------------------------------

#: web 侧三处版本副本相对仓库根的路径。
WEB_PACKAGE_JSON = Path("apps/web") / "package.json"
WEB_PACKAGE_LOCK = Path("apps/web") / "package-lock.json"
UV_LOCK = Path("uv.lock")

#: ``uv.lock`` 的包段头（``[[package]]``）。
_UVLOCK_PACKAGE_HEADER_RE = re.compile(r"^\[\[package\]\]\s*$", re.MULTILINE)
#: TOML 的字符串标量（``name = "..."`` / ``version = "..."``）。
_TOML_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]*)"', re.MULTILINE)
_TOML_VERSION_RE = re.compile(r'^\s*version\s*=\s*"([^"]*)"', re.MULTILINE)


def _read_uvlock_version(repo_root: Path) -> str | None:
    """读 ``uv.lock`` 里 ``name = "novelos"`` 那个包的 ``version``。

    不用 ``tomllib``：与 :func:`_read_pyproject_version` 同款理由（本脚本声明
    「Python 3.9+ 标准库」而 ``tomllib`` 是 3.11 才进标准库）。这里只需要一个
    扁平键 ⇒ 按 ``[[package]]`` 段切开，再在**段头**（第一个子表 ``[package.*]``
    之前）里取 ``name`` / ``version``。
    """
    path = repo_root / UV_LOCK
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    starts = [m.end() for m in _UVLOCK_PACKAGE_HEADER_RE.finditer(text)]
    starts.append(len(text))
    for begin, end in zip(starts, starts[1:]):
        segment = text[begin:end]
        name_match = _TOML_NAME_RE.search(segment)
        if name_match is None or name_match.group(1) != "novelos":
            continue
        head = segment.split("\n[", 1)[0]  # 只看段头，别撞上 [package.*] 子表
        version_match = _TOML_VERSION_RE.search(head)
        return version_match.group(1) if version_match else None
    return None


def _web_version_check(
    check_id: str,
    rel: Path,
    app_version: str,
    declared: list[tuple[str, str | None]],
) -> Check:
    """一处版本副本的比对结果（**逐字段**列出实测值）。"""
    mismatched = [(label, value) for label, value in declared if value != app_version]
    if not mismatched:
        detail = "、".join(f"{label}={value!r}" for label, value in declared)
        return Check(
            check_id,
            True,
            f"{rel} {detail} 与 pyproject [project].version={app_version!r} 一致",
        )
    detail = "、".join(
        f"{label}={value!r}" + ("（字段缺失或非字符串）" if value is None else "")
        for label, value in mismatched
    )
    return Check(
        check_id,
        False,
        f"{rel} {detail} ≠ pyproject [project].version={app_version!r}（发版漏同步）",
    )


def _check_web_version_copies(repo_root: Path) -> list[Check]:
    """web 三处版本副本必须与 ``pyproject`` 的 ``[project].version`` **逐字一致**。

    为什么补这三处
    --------------
    2026-10-23 实测（task-4 / D-16）：发版纪律要求**六处**版本同步
    （``pyproject`` / ``apps/web/package.json`` / ``apps/web/package-lock.json`` /
    ``uv.lock`` / ``.claude-plugin/plugin.json`` / ``skills/novelos/SKILL.md``
    的 ``metadata.novelosVersion``），而本脚本此前只校其中**三处**
    ⇒ **web 三处零看守**：任一处漏同步都没有任何机器拦它。

    口径
    ----
    - **参照系 = ``pyproject`` 的 ``[project].version``**（发版纪律的**单一源**，
      AGENTS.md「版本纪律」明写）⇒ 与 SKILL.md 同键副本那条互为交叉验证。
    - **文件不存在 ⇒ 跳过**（不制造假红）：纯分发上下文（skill 包单独抽出）里
      根本没有 ``apps/web`` 与 ``uv.lock``，与既有「清单缺失就 continue」同款。
    - **文件在、但版本字段缺失/非字符串 ⇒ 判红**（副本存在却没声明版本 = 漏同步）。
    """
    app_version = _read_pyproject_version(repo_root)
    if app_version is None:
        # 没有 pyproject ⇒ 没有「单一源」可比 ⇒ 跳过（与既有 pyproject 比对同款口径）。
        return []

    checks: list[Check] = []

    # ① apps/web/package.json —— 顶层 version
    rel = WEB_PACKAGE_JSON
    path = repo_root / rel
    if path.is_file():
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(
                Check("app-version-consistency/web-package-json", False, f"{rel} 读取失败：{exc}")
            )
        else:
            raw = data.get("version") if isinstance(data, dict) else None
            declared = [("version", raw if isinstance(raw, str) else None)]
            checks.append(
                _web_version_check(
                    "app-version-consistency/web-package-json", rel, app_version, declared
                )
            )

    # ② apps/web/package-lock.json —— 顶层 version + packages[""].version（两个副本）
    rel = WEB_PACKAGE_LOCK
    path = repo_root / rel
    if path.is_file():
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(
                Check("app-version-consistency/web-package-lock", False, f"{rel} 读取失败：{exc}")
            )
        else:
            top = data.get("version") if isinstance(data, dict) else None
            packages = data.get("packages") if isinstance(data, dict) else None
            root_pkg = packages.get("") if isinstance(packages, dict) else None
            root_version = root_pkg.get("version") if isinstance(root_pkg, dict) else None
            declared = [
                ("version", top if isinstance(top, str) else None),
                ('packages[""].version', root_version if isinstance(root_version, str) else None),
            ]
            checks.append(
                _web_version_check(
                    "app-version-consistency/web-package-lock", rel, app_version, declared
                )
            )

    # ③ uv.lock —— name = "novelos" 段的 version
    rel = UV_LOCK
    if (repo_root / rel).is_file():
        checks.append(
            _web_version_check(
                "app-version-consistency/uv-lock",
                rel,
                app_version,
                [('name = "novelos" 段的 version', _read_uvlock_version(repo_root))],
            )
        )

    return checks


def check_app_version_consistency(skill_dir: Path, repo_root: Path) -> list[Check]:
    """``metadata.novelosVersion`` 必须与 app 版本、各平台清单里的同键副本一致。

    为什么单列一条检查
    ------------------
    ``check_version_consistency`` 比的是 ``metadata.version``——那是 **skill 包自身**的
    版本号（0.1.0），与 NovelOS 应用的版本是两条线。真正声明「本包面向哪个 app 版本」的
    是 ``metadata.novelosVersion``，它与 ``.claude-plugin/plugin.json`` 的
    ``metadata.novelosVersion`` **同键同义** ⇒ 发版时是**第 6 处**必须同步的副本，
    此前没有任何机器校验。

    2026-10-22 实测（批次十九）：app 已是 ``3.10.0``（``pyproject`` 与 ``plugin.json``
    都是 3.10.0），而 ``skills/novelos/SKILL.md`` 仍写 ``3.9.0`` 且长期无人发现——
    因为唯一的相关检查只盯 ``metadata.version``，而它恰好是对的。

    口径
    ----
    - 与各平台清单的同键副本比对：**两份都在时必查**（纯分发副本也能自查）。
    - 与 ``pyproject.toml`` 的 ``[project].version`` 比对：**仓库里有 pyproject 时才查**；
      纯分发上下文没有 pyproject ⇒ 跳过，不当失败（与 ``check_version_consistency``
      「清单缺失就 continue」同款，不制造假红）。
    - **web 三处**（``apps/web/package.json`` / ``apps/web/package-lock.json`` /
      ``uv.lock``）同样以 ``pyproject`` 的 ``[project].version`` 为**单一源**比对
      （见 :func:`_check_web_version_copies` 的口径说明）。
    """
    checks: list[Check] = []
    # web 三处版本副本：与 SKILL.md 无关（SKILL.md 缺失时也要查得到它们）。
    checks.extend(_check_web_version_copies(repo_root))

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        checks.append(
            Check(
                "app-version-consistency/skill",
                False,
                "缺少 SKILL.md，无法比对 metadata.novelosVersion",
            )
        )
        return checks
    try:
        front, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    except ValueError as exc:
        checks.append(Check("app-version-consistency/skill", False, f"frontmatter 解析失败：{exc}"))
        return checks
    metadata = front.get("metadata")
    declared = metadata.get("novelosVersion") if isinstance(metadata, dict) else None
    checks.extend(
        [
            Check(
                "app-version-consistency/skill",
                isinstance(declared, str) and bool(declared),
                (
                    f"SKILL.md metadata.novelosVersion = {declared!r}"
                    if isinstance(declared, str)
                    else f"SKILL.md metadata.novelosVersion 缺失或非字符串：{declared!r}"
                ),
            )
        ]
    )

    for platform, rel in PLUGIN_MANIFESTS.items():
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(
                Check(f"app-version-consistency/{platform}", False, f"{rel} 读取失败：{exc}")
            )
            continue
        manifest_meta = data.get("metadata") if isinstance(data, dict) else None
        manifest_declared = (
            manifest_meta.get("novelosVersion") if isinstance(manifest_meta, dict) else None
        )
        if manifest_declared is None:
            continue
        ok = isinstance(declared, str) and manifest_declared == declared
        checks.append(
            Check(
                f"app-version-consistency/{platform}",
                ok,
                (
                    f"{rel} metadata.novelosVersion={manifest_declared!r} 与 SKILL.md 逐字一致"
                    if ok
                    else f"{rel} metadata.novelosVersion={manifest_declared!r} ≠ "
                    f"SKILL.md metadata.novelosVersion={declared!r}"
                ),
            )
        )

    app_version = _read_pyproject_version(repo_root)
    if app_version is not None:
        ok = isinstance(declared, str) and declared == app_version
        checks.append(
            Check(
                "app-version-consistency/pyproject",
                ok,
                (
                    f"SKILL.md metadata.novelosVersion 与 pyproject [project].version="
                    f"{app_version!r} 一致"
                    if ok
                    else f"SKILL.md metadata.novelosVersion={declared!r} ≠ "
                    f"pyproject [project].version={app_version!r}（发版漏同步）"
                ),
            )
        )
    return checks


def check_references(skill_dir: Path) -> list[Check]:
    """SKILL.md 引用的资源都存在；且 references/ 与 scripts/ 里没有孤儿文件。"""
    checks: list[Check] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [Check("references/skill-md", False, "缺少 SKILL.md")]
    text = skill_md.read_text(encoding="utf-8")
    referenced = sorted(set(REF_MD_RE.findall(text)) | set(REF_PY_RE.findall(text)))
    checks.append(
        Check(
            "references/discovered",
            bool(referenced),
            f"SKILL.md 引用了 {len(referenced)} 个资源"
            if referenced
            else "SKILL.md 没有引用任何 references/*.md 或 scripts/*.py",
        )
    )
    for rel in referenced:
        path = skill_dir / rel
        checks.append(
            Check(
                f"references/exists/{rel}",
                path.is_file(),
                f"{rel} 存在" if path.is_file() else f"SKILL.md 引用了 {rel}，但该文件不存在",
            )
        )
    for folder, pattern in (("references", "*.md"), ("scripts", "*.py")):
        directory = skill_dir / folder
        if not directory.is_dir():
            continue
        on_disk = sorted(f"{folder}/{p.name}" for p in directory.glob(pattern) if p.is_file())
        orphans = [rel for rel in on_disk if rel not in referenced]
        checks.append(
            Check(
                f"references/no-orphans/{folder}",
                not orphans,
                (
                    f"{folder}/ 下 {len(on_disk)} 个文件全部被 SKILL.md 引用"
                    if not orphans
                    else f"{folder}/ 存在未被 SKILL.md 引用的孤儿文件：{orphans}"
                ),
            )
        )
    return checks


def check_client_help_offline(skill_dir: Path) -> list[Check]:
    """``novelos.py --help`` 必须在**断网**状态下退出码 0。"""
    script = skill_dir / "scripts" / "novelos.py"
    if not script.is_file():
        return [Check("client-help-offline", False, f"缺少 {script}")]
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "sitecustomize.py").write_text(_BLOCK_SOCKET_SRC, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([tmp, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            proc = subprocess.run(  # noqa: S603 - 命令固定
                [sys.executable, str(script), "--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return [Check("client-help-offline", False, f"执行 novelos.py --help 失败：{exc}")]
    ok = proc.returncode == 0 and "usage" in (proc.stdout or "").lower()
    return [
        Check(
            "client-help-offline",
            ok,
            (
                f"novelos.py --help 离线退出码 {proc.returncode}，输出含 usage"
                if ok
                else f"novelos.py --help 离线退出码 {proc.returncode}；"
                f"stdout 前 200 字：{(proc.stdout or '')[:200]!r}；"
                f"stderr 前 200 字：{(proc.stderr or '')[:200]!r}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def run_all_checks(skill_dir: Path, repo_root: Path) -> list[Check]:
    checks: list[Check] = []
    checks.extend(check_manifests_exist(repo_root))
    checks.extend(check_manifest_fields(repo_root))
    checks.extend(check_marketplace_shapes(repo_root))
    checks.extend(check_frontmatter(skill_dir))
    checks.extend(check_version_consistency(skill_dir, repo_root))
    checks.extend(check_app_version_consistency(skill_dir, repo_root))
    checks.extend(check_references(skill_dir))
    checks.extend(check_client_help_offline(skill_dir))
    return checks


# ---------------------------------------------------------------------------
# --selftest：造坏副本，断言自己会报红
# ---------------------------------------------------------------------------


def _copy_tree(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _build_bad_copy(repo_root: Path, skill_dir: Path, dest: Path) -> None:
    """把 skill 包与三平台清单复制到 ``dest``，制造一个**结构相同**的副本。

    ⚠️ 2026-10-23（task-4 / 对抗审查 A2）：**web 三处版本副本 + 单一源 pyproject
    也必须复制进来**。理由：
      ① 不复制 ⇒ ``_check_web_version_copies`` 命中「文件不存在 ⇒ 跳过」的口径
         ⇒ **整条 web 检查在坏副本里不跑** ⇒ ``--selftest`` 变成「跑而不验」
         （CI 的「Contract self-checks」那一步**只跑本脚本** ⇒ 新看守在 CI 上失守）。
      ② ``pyproject.toml`` 还是 web 三处的**参照系**（单一源）⇒ 没有它就没法比。
    """
    _copy_tree(skill_dir, dest / "skills" / skill_dir.name)
    for rel in PLUGIN_MANIFESTS.values():
        _copy_tree(repo_root / rel, dest / rel)
    for rel in MARKETPLACE_MANIFESTS.values():
        _copy_tree(repo_root / rel, dest / rel)
    # web 三处版本副本 + 单一源（pyproject）
    for rel in (Path("pyproject.toml"), WEB_PACKAGE_JSON, WEB_PACKAGE_LOCK, UV_LOCK):
        _copy_tree(repo_root / rel, dest / rel)


def _mutate_skill_md(path: Path, mutation: str) -> None:
    text = path.read_text(encoding="utf-8")
    if mutation == "top-level-version":
        text = text.replace(
            "license: AGPL-3.0",
            "license: AGPL-3.0\nversion: \"0.1.0\"",
            1,
        )
    elif mutation == "version-mismatch":
        text = text.replace('version: "0.1.0"', 'version: "9.9.9"', 1)
    elif mutation == "bad-name":
        text = text.replace("name: novelos", "name: Novel_OS", 1)
    elif mutation == "novelos-version-mismatch":
        # 第 6 处版本副本：这个变异由「SKILL.md ↔ plugin.json 同键比对」那条抓
        # （app-version-consistency/claude）。
        # ⚠️ 2026-10-23（task-4 / 审查 A2）起，坏副本**也复制 pyproject.toml** ⇒
        # 「SKILL.md ↔ pyproject」那条（app-version-consistency/pyproject）会**同时**报红
        # ——两条都红是预期的（本变异确实漏同步了两处），selftest 只要求**预期那条**红。
        text = re.sub(
            r'novelosVersion:\s*"[^"]*"',
            'novelosVersion: "9.9.9"',
            text,
            count=1,
        )
    path.write_text(text, encoding="utf-8")


def _mutate_cursor_manifest(path: Path) -> None:
    data = _load_json(path)
    data["metadata"] = {"version": "0.1.0"}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mutate_claude_marketplace(path: Path) -> None:
    data = _load_json(path)
    entries = data.get("plugins")
    if isinstance(entries, list) and entries:
        entries[0]["source"] = "skills/novelos"  # 少了 './' 前缀
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


#: web 三处版本副本的**假版本**（刻意与单一源不同 ⇒ 比对必须报红）。
_FAKE_APP_VERSION = "9.9.9"


def _mutate_web_package_json(path: Path) -> None:
    """把 ``apps/web/package.json`` 的顶层 ``version`` 改成假版本。"""
    data = _load_json(path)
    data["version"] = _FAKE_APP_VERSION
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mutate_web_package_lock(path: Path, *, where: str) -> None:
    """把 ``apps/web/package-lock.json`` 的某个 ``version`` 改成假版本。

    ``where="top"`` ⇒ 顶层 ``version``；``where="root"`` ⇒ ``packages[""].version``。
    两个副本**各自单测**：只测其一时，另一处若漏检就抓不到。
    """
    data = _load_json(path)
    if where == "top":
        data["version"] = _FAKE_APP_VERSION
    elif where == "root":
        data["packages"][""]["version"] = _FAKE_APP_VERSION
    else:
        raise ValueError(f"未知的 where：{where!r}")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mutate_uv_lock(path: Path) -> None:
    """把 ``uv.lock`` 里 ``name = "novelos"`` 段的 ``version`` 改成假版本。"""
    text = path.read_text(encoding="utf-8")
    m = re.search(r'(name = "novelos"\s*\nversion = )"[^"]*"', text)
    if m is None:
        raise AssertionError('uv.lock 里找不到 name = "novelos" 段（变异锚点失效）')
    text = text[: m.start(1)] + m.group(1) + f'"{_FAKE_APP_VERSION}"' + text[m.end() :]
    path.write_text(text, encoding="utf-8")


def run_selftest(skill_dir: Path, repo_root: Path) -> tuple[bool, list[str]]:
    """变异验证：每个变异都必须让**对应**检查报红。"""
    log: list[str] = []
    ok = True

    baseline = run_all_checks(skill_dir, repo_root)
    baseline_failures = [c for c in baseline if not c.ok]
    if baseline_failures:
        ok = False
        log.append(
            "FAIL 原始树本应全绿，实际有 "
            f"{len(baseline_failures)} 项红："
            + "；".join(f"{c.check_id}: {c.message}" for c in baseline_failures)
        )
    else:
        log.append(f"PASS 原始树全绿（{len(baseline)} 项检查）")

    mutations: list[tuple[str, str, str]] = [
        (
            "M1 顶层 version",
            "frontmatter-no-top-level-version",
            "skill-md:top-level-version",
        ),
        (
            "M2 版本不一致",
            "version-consistency/claude",
            "skill-md:version-mismatch",
        ),
        (
            "M3 name 不合规",
            "frontmatter-name-format",
            "skill-md:bad-name",
        ),
        (
            "M4 cursor 混入 metadata",
            "manifest-fields/cursor",
            "cursor-manifest",
        ),
        (
            "M5 claude source 缺 './'",
            "marketplace-source/claude[0]",
            "claude-marketplace",
        ),
        (
            "M6 references 孤儿文件",
            "references/no-orphans/references",
            "orphan-reference",
        ),
        (
            "M7 引用指向不存在的文件",
            "references/exists/references/quality.md",
            "missing-reference",
        ),
        (
            "M8 novelosVersion 与 app 版本不一致（第 6 处版本副本）",
            "app-version-consistency/claude",
            "skill-md:novelos-version-mismatch",
        ),
        (
            "M9 web 版 package.json 的 version 与单一源不一致",
            "app-version-consistency/web-package-json",
            "web-package-json",
        ),
        (
            "M10 web 版 package-lock.json 顶层 version 与单一源不一致",
            "app-version-consistency/web-package-lock",
            "web-package-lock-top",
        ),
        (
            "M11 web 版 package-lock.json packages[\"\"].version 与单一源不一致",
            "app-version-consistency/web-package-lock",
            "web-package-lock-root",
        ),
        (
            "M12 uv.lock 里 novelos 段的 version 与单一源不一致",
            "app-version-consistency/uv-lock",
            "uv-lock",
        ),
    ]

    for title, expected_id, kind in mutations:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "repo"
            _build_bad_copy(repo_root, skill_dir, dest)
            bad_skill = dest / "skills" / skill_dir.name
            if kind.startswith("skill-md:"):
                _mutate_skill_md(bad_skill / "SKILL.md", kind.split(":", 1)[1])
            elif kind == "cursor-manifest":
                _mutate_cursor_manifest(dest / PLUGIN_MANIFESTS["cursor"])
            elif kind == "claude-marketplace":
                _mutate_claude_marketplace(dest / MARKETPLACE_MANIFESTS["claude"])
            elif kind == "orphan-reference":
                (bad_skill / "references" / "orphan_extra.md").write_text(
                    "# 孤儿文件\n\nSKILL.md 没有引用本文件。\n", encoding="utf-8"
                )
            elif kind == "missing-reference":
                (bad_skill / "references" / "quality.md").unlink()
            elif kind == "web-package-json":
                _mutate_web_package_json(dest / WEB_PACKAGE_JSON)
            elif kind == "web-package-lock-top":
                _mutate_web_package_lock(dest / WEB_PACKAGE_LOCK, where="top")
            elif kind == "web-package-lock-root":
                _mutate_web_package_lock(dest / WEB_PACKAGE_LOCK, where="root")
            elif kind == "uv-lock":
                _mutate_uv_lock(dest / UV_LOCK)
            checks = run_all_checks(bad_skill, dest)
            hit = [c for c in checks if c.check_id == expected_id and not c.ok]
            if hit:
                log.append(f"PASS {title} → 预期检查 {expected_id} 已报红：{hit[0].message}")
            else:
                ok = False
                fired = [c.check_id for c in checks if not c.ok]
                log.append(
                    f"FAIL {title} → 预期检查 {expected_id} **没有**报红"
                    f"（实际报红的检查：{fired or '无'}）"
                )
    return ok, log


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _default_paths() -> tuple[Path, Path]:
    here = Path(__file__).resolve()
    skill_dir = here.parents[1]           # skills/novelos
    repo_root = here.parents[3]           # <repo>
    return skill_dir, repo_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_skill_package.py",
        description="Agent Skill 包契约自检（stdlib-only）。",
    )
    parser.add_argument("--skill-dir", help="skill 包目录（默认取脚本自身位置）")
    parser.add_argument("--repo-root", help="仓库根目录（默认取脚本自身位置）")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="额外做变异验证：造坏副本并断言本脚本会报红",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    default_skill, default_repo = _default_paths()
    skill_dir = Path(args.skill_dir).expanduser().resolve() if args.skill_dir else default_skill
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else default_repo

    if not (skill_dir / "SKILL.md").is_file():
        sys.stderr.write(f"不是 skill 包目录（缺 SKILL.md）：{skill_dir}\n")
        return 2

    checks = run_all_checks(skill_dir, repo_root)
    failures = [c for c in checks if not c.ok]

    selftest_ok = True
    selftest_log: list[str] = []
    if args.selftest:
        selftest_ok, selftest_log = run_selftest(skill_dir, repo_root)

    if args.json:
        payload = {
            "skill_dir": str(skill_dir),
            "repo_root": str(repo_root),
            "total": len(checks),
            "failed": len(failures),
            "checks": [c.as_dict() for c in checks],
            "selftest": {"ok": selftest_ok, "log": selftest_log} if args.selftest else None,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        for c in checks:
            mark = "PASS" if c.ok else "FAIL"
            sys.stdout.write(f"[{mark}] {c.check_id}: {c.message}\n")
        if args.selftest:
            sys.stdout.write("\n--- selftest（变异验证）---\n")
            for line in selftest_log:
                sys.stdout.write(line + "\n")

    if failures:
        sys.stderr.write(f"\n{len(failures)}/{len(checks)} 项检查未通过。\n")
        return 1
    if args.selftest and not selftest_ok:
        sys.stderr.write("\nselftest 失败：变异未被捕获。\n")
        return 1
    if not args.json:
        suffix = "（含 selftest）" if args.selftest else ""
        sys.stdout.write(f"\n全部 {len(checks)} 项检查通过{suffix}。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
