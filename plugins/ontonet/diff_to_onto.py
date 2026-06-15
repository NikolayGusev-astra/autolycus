"""
plugins/ontonet.diff_to_onto — git diff → Onto [UPD] sync.

Dry-run by design. Apply mode creates/updates Onto entities and relations only
when explicitly requested through the CLI.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from plugins.ontonet import DEFAULT_REALM_ID, get_client

logger = logging.getLogger(__name__)

HOOK_NAMES = (
    "before_persist_message",
    "before_persist_system_prompt",
    "after_llm_response",
    "post_activity",
    "pre_tool_call",
    "post_tool_call",
    "transform_tool_result",
    "transform_llm_output",
    "pre_llm_call",
    "post_llm_call",
    "pre_gateway_dispatch",
)

CORE_FILES: Dict[str, Tuple[str, str]] = {
    "run_agent.py": ("run_agent", "[CORE] Модуль ядра"),
    "model_tools.py": ("model_tools", "[CORE] Модуль ядра"),
    "cli.py": ("cli", "[CORE] Модуль ядра"),
    "toolsets.py": ("toolsets", "[CORE] Модуль ядра"),
    "hermes_state.py": ("hermes_state", "[CORE] Модуль ядра"),
    "hermes_constants.py": ("hermes_constants", "[CORE] Модуль ядра"),
    "hermes_logging.py": ("hermes_logging", "[CORE] Модуль ядра"),
    "batch_runner.py": ("batch_runner", "[CORE] Модуль ядра"),
}

GATEWAY_FILES: Dict[str, Tuple[str, str]] = {
    "gateway/run.py": ("GatewayRunner", "[GW] Модуль Gateway"),
    "gateway/discovery.py": ("discovery.py", "[GW] Модуль Gateway"),
    "gateway/chat.py": ("chat.py", "[GW] Модуль Gateway"),
    "gateway/config.py": ("config.py", "[GW] Модуль Gateway"),
}

FRONTEND_FILES: Dict[str, Tuple[str, str]] = {
    "src/App.tsx": ("App.tsx", "[FRONT] Frontend"),
    "src/main.tsx": ("main.tsx", "[FRONT] Frontend"),
    "src/lib.rs": ("lib.rs", "[RUST] Rust module"),
}

PLUGIN_ALIASES = {
    "ontonet": "ontonet",
    "redactor": "redactor",
    "credits_notices": "credits_notices",
    "kanban_heartbeat": "kanban_heartbeat",
    "rtk": "rtk",
    "rtk_ck": "rtk_ck",
    "sbl": "sbl",
    "governance": "governance",
    "tacops": "tacops",
    "kanban": "kanban",
}

RISKY_FILES = {
    "run_agent.py",
    "hermes_cli/plugins.py",
    "model_tools.py",
    "gateway/run.py",
    "plugins/rtk_ck/compress.py",
    "plugins/rtk_ck/context_engine.py",
    "plugins/rtk_ck/result_cache.py",
}



@dataclass
class Hunk:
    header: str
    added: int = 0
    removed: int = 0
    text: str = ""

    @property
    def risk_reasons(self) -> List[str]:
        reasons: List[str] = []
        lowered = self.text.lower()
        if "valid_hooks" in lowered or "invoke_hook" in lowered or "register_hook" in lowered:
            reasons.append("hook plumbing")
        for hook in HOOK_NAMES:
            if hook in self.text:
                reasons.append(f"hook:{hook}")
                break
        if any(token in lowered for token in ("persist_system_prompt", "after_llm_response", "pre_tool_call")):
            reasons.append("state/tool contract")
        return reasons


@dataclass
class FileChange:
    status: str
    path: str
    old_path: Optional[str] = None
    hunks: List[Hunk] = field(default_factory=list)

    @property
    def added_lines(self) -> int:
        return sum(h.added for h in self.hunks)

    @property
    def removed_lines(self) -> int:
        return sum(h.removed for h in self.hunks)

    @property
    def risk_reasons(self) -> List[str]:
        reasons: List[str] = []
        if self.path in RISKY_FILES:
            reasons.append("risky file")
        for hunk in self.hunks:
            lowered = hunk.text.lower()
            if "valid_hooks" in lowered or "invoke_hook" in lowered or "register_hook" in lowered:
                reasons.append("hook plumbing")
            if self.path in RISKY_FILES or self.path.startswith(("run_agent.py", "hermes_cli/", "gateway/", "plugins/")):
                for hook in HOOK_NAMES:
                    if hook in hunk.text:
                        reasons.append(f"hook:{hook}")
                        break
            if self.path in {"run_agent.py", "hermes_state.py", "model_tools.py", "plugins/rtk_ck/result_cache.py"}:
                if any(token in lowered for token in ("persist_system_prompt", "after_llm_response", "pre_tool_call")):
                    reasons.append("state/tool contract")
        return sorted(set(reasons))

    @property
    def risk_level(self) -> str:
        if self.risk_reasons:
            return "HIGH" if self.path in RISKY_FILES else "MEDIUM"
        return "LOW"


@dataclass
class EntityRef:
    name: str
    entity_id: Optional[str]
    template: str
    created: bool = False


@dataclass
class PlannedRelation:
    relation_type: str
    source_name: str
    source_id: Optional[str]
    target_name: str
    target_id: Optional[str]


@dataclass
class DiffPlan:
    repo: str
    base: str
    head: str
    files: List[FileChange]
    targets: Dict[str, EntityRef] = field(default_factory=dict)
    relations: List[PlannedRelation] = field(default_factory=list)


def run_git(repo: str, args: Sequence[str], timeout: int = 60) -> str:
    cmd = ["git", "-C", repo, *args]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"git command failed: {' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc.stdout


def _diff_range(base: str, head: str) -> str:
    return f"{base}...{head}"


def _diff_range_fallback(base: str, head: str) -> str:
    return f"{base}..{head}"


def parse_name_status(repo: str, base: str, head: str) -> List[FileChange]:
    range_expr = _diff_range(base, head)
    try:
        output = run_git(repo, ["diff", "--name-status", "--find-renames", "--find-copies", range_expr])
    except RuntimeError:
        range_expr = _diff_range_fallback(base, head)
        output = run_git(repo, ["diff", "--name-status", "--find-renames", "--find-copies", range_expr])
    changes: List[FileChange] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") or status.startswith("C"):
            old_path = parts[1]
            path = parts[2] if len(parts) > 2 else old_path
        else:
            old_path = None
            path = parts[1] if len(parts) > 1 else ""
        if path:
            changes.append(FileChange(status=status, path=path, old_path=old_path))
    return changes


def parse_unified_diff(repo: str, base: str, head: str) -> Dict[str, List[Hunk]]:
    range_expr = _diff_range(base, head)
    try:
        output = run_git(repo, ["diff", "--unified=0", "--find-renames", "--find-copies", range_expr])
    except RuntimeError:
        range_expr = _diff_range_fallback(base, head)
        output = run_git(repo, ["diff", "--unified=0", "--find-renames", "--find-copies", range_expr])
    by_file: Dict[str, List[Hunk]] = {}
    current_path: Optional[str] = None
    current_hunk: Optional[Hunk] = None

    header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
    for raw_line in output.splitlines():
        if raw_line.startswith("diff --git "):
            current_path = None
            current_hunk = None
            continue
        if raw_line.startswith("+++ b/"):
            current_path = raw_line[6:]
            by_file.setdefault(current_path, [])
            current_hunk = None
            continue
        if raw_line.startswith("+++ /dev/null"):
            current_hunk = None
            continue
        match = header_re.match(raw_line)
        if match:
            header = raw_line
            removed = int(match.group(2) or "1")
            added = int(match.group(4) or "1")
            current_hunk = Hunk(header=header, added=added, removed=removed)
            if current_path:
                by_file.setdefault(current_path, []).append(current_hunk)
            continue
        if current_hunk is not None:
            current_hunk.text += raw_line + "\n"
    return by_file


def attach_hunks(changes: List[FileChange], hunks_by_file: Dict[str, List[Hunk]]) -> List[FileChange]:
    by_path = {c.path: c for c in changes}
    for path, hunks in hunks_by_file.items():
        change = by_path.get(path)
        if change:
            change.hunks = hunks
    return changes


def repo_root_from(start: Optional[str] = None) -> str:
    cwd = Path(start or os.getcwd()).resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return str(parent)
    return str(cwd)


def map_file_to_entity(path: str) -> Optional[Tuple[str, str, str]]:
    if path in CORE_FILES:
        name, template = CORE_FILES[path]
        return name, template, "CORE"
    if path in GATEWAY_FILES:
        name, template = GATEWAY_FILES[path]
        return name, template, "GW"
    if path in FRONTEND_FILES:
        name, template = FRONTEND_FILES[path]
        return name, template, "FRONT" if path.endswith(".tsx") else "RUST"
    if path.startswith("plugins/"):
        parts = path.split("/")
        if len(parts) >= 2 and parts[1] in PLUGIN_ALIASES:
            name = PLUGIN_ALIASES[parts[1]]
            return name, f"[PLGN] Плагин {name}", "PLGN"
    if path.startswith("scripts/"):
        return "scripts", "[TOOL] Инструмент", "TOOL"
    if path.startswith("tests/"):
        return "tests", "[TEST] Тест", "TEST"
    return None


def collect_targets(changes: Sequence[FileChange]) -> Dict[str, EntityRef]:
    targets: Dict[str, EntityRef] = {}
    for change in changes:
        mapped = map_file_to_entity(change.path)
        if mapped:
            name, template, _kind = mapped
            targets.setdefault(name, EntityRef(name=name, entity_id=None, template=template))
        if change.risk_level in {"MEDIUM", "HIGH"}:
            risk_name = f"Risk: upstream change in {change.path}"
            targets.setdefault(risk_name, EntityRef(name=risk_name, entity_id=None, template="[RISK] Риск"))
        for reason in change.risk_reasons:
            if reason.startswith("hook:"):
                hook = reason.split(":", 1)[1]
                targets.setdefault(hook, EntityRef(name=hook, entity_id=None, template="[HOOK] Хук интеграции"))
    return targets


def _update_label(base: str, head: str) -> str:
    return f"{base}...{head}"


def build_plan(repo: str, base: str, head: str, limit: Optional[int] = None) -> DiffPlan:
    changes = parse_name_status(repo, base, head)
    if limit:
        changes = changes[:limit]
    hunks = parse_unified_diff(repo, base, head)
    changes = attach_hunks(changes, hunks)
    targets = collect_targets(changes)
    relations: List[PlannedRelation] = []
    update_label = _update_label(base, head)
    for change in changes:
        upd_name = f"[UPD] upstream {update_label}: {change.path}"
        targets.setdefault(upd_name, EntityRef(name=upd_name, entity_id=None, template="[UPD] Upstream изменение"))
        mapped = map_file_to_entity(change.path)
        if mapped:
            target_name, _template, _kind = mapped
            relations.append(PlannedRelation("patches", upd_name, None, target_name, None))
        if change.path.startswith("plugins/"):
            plugin_name = PLUGIN_ALIASES.get(change.path.split("/")[1], change.path.split("/")[1])
            relations.append(PlannedRelation("conflicts_with", upd_name, None, plugin_name, None))
        for reason in change.risk_reasons:
            if reason.startswith("hook:"):
                hook = reason.split(":", 1)[1]
                relations.append(PlannedRelation("hooks_into", upd_name, None, hook, None))
            else:
                risk_name = f"Risk: upstream change in {change.path}"
                relations.append(PlannedRelation("breaks", upd_name, None, risk_name, None))
    return DiffPlan(repo=repo, base=base, head=head, files=changes, targets=targets, relations=relations)


def _search_templates(client: Any, realm: str, name_part: str) -> List[str]:
    raw = client.search_templates(realm, name_part=name_part)
    if not raw:
        return []
    text = str(raw)
    return re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)


def _search_entities(client: Any, realm: str, name_filter: str, meta_entity_id: Optional[str] = None) -> List[str]:
    raw = client.search_entities(
        realm_id=realm,
        name_filter=name_filter,
        meta_entity_id=meta_entity_id,
        limit=20,
    )
    if not raw:
        return []
    text = str(raw)
    return re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)


def ensure_template(client: Any, realm: str, name: str, description: str) -> str:
    ids = _search_templates(client, realm, name)
    if ids:
        return ids[0]
    eid = client.save_template(realm, name=name, description=description)
    if not eid:
        raise RuntimeError(f"Failed to ensure template {name!r}")
    time.sleep(0.35)
    return eid


def ensure_entity(client: Any, realm: str, name: str, description: str, template_id: Optional[str]) -> Tuple[str, bool]:
    ids = _search_entities(client, realm, name, meta_entity_id=template_id)
    if ids:
        return ids[0], False
    eid = client.save_entity(realm, name=name, description=description, meta_entity_id=template_id)
    if not eid:
        raise RuntimeError(f"Failed to ensure entity {name!r}")
    time.sleep(0.35)
    return eid, True


def ensure_relation(
    client: Any,
    realm: str,
    source_id: str,
    target_id: str,
    relation_type: str,
    force: bool = False,
) -> Optional[str]:
    if not force:
        # Onto MCP does not expose a reliable search_relations in the current plugin.
        # Avoid accidental duplicate storms unless caller explicitly opts in.
        return None
    rid = client.create_relation(realm, source_id, target_id, relation_type)
    if rid:
        time.sleep(0.5)
    return rid


def apply_plan(
    plan: DiffPlan,
    realm: str = DEFAULT_REALM_ID,
    diagram_id: Optional[str] = None,
    force_relations: bool = False,
    sleep: float = 0.35,
) -> Dict[str, Any]:
    client = get_client()
    templates: Dict[str, str] = {}
    entities: Dict[str, str] = {}
    created_entities: List[str] = []
    created_relations: List[str] = []

    template_specs = {
        "[CORE] Модуль ядра": "Модуль ядра Hermes/Autolycus",
        "[GW] Модуль Gateway": "Gateway-модуль",
        "[PLGN] Плагин ontonet": "Плагин",
        "[HOOK] Хук интеграции": "Хук интеграции",
        "[RISK] Риск": "Риск мержа",
        "[UPD] Upstream изменение": "Изменение upstream",
        "[TOOL] Инструмент": "Инструмент разработки",
        "[TEST] Тест": "Тест",
        "[FRONT] Frontend": "Frontend-модуль",
        "[RUST] Rust module": "Rust-модуль",
    }
    # Normalize plugin templates dynamically.
    for ref in plan.targets.values():
        if ref.template.startswith("[PLGN]"):
            template_specs.setdefault(ref.template, "Плагин")
        elif ref.template not in template_specs:
            template_specs[ref.template] = ref.template

    for template_name, description in template_specs.items():
        if any(ref.template == template_name for ref in plan.targets.values()) or template_name.startswith("[UPD]"):
            templates[template_name] = ensure_template(client, realm, template_name, description)
            time.sleep(sleep)

    for name, ref in plan.targets.items():
        template_id = templates.get(ref.template)
        description = f"Managed by plugins/ontonet diff_to_onto. base={plan.base} head={plan.head}."
        eid, created = ensure_entity(client, realm, name, description, template_id)
        entities[name] = eid
        ref.entity_id = eid
        ref.created = created
        if created:
            created_entities.append(name)
        time.sleep(sleep)

    for relation in plan.relations:
        source_id = entities.get(relation.source_name)
        target_id = entities.get(relation.target_name)
        if not source_id or not target_id:
            continue
        rid = ensure_relation(client, realm, source_id, target_id, relation.relation_type, force=force_relations)
        if rid:
            created_relations.append(rid)

    if diagram_id:
        node_ids = [eid for eid in entities.values() if eid]
        if node_ids:
            client.add_nodes_to_diagram(realm, diagram_id, node_ids)
            time.sleep(sleep)

    return {
        "created_entities": created_entities,
        "created_relations": created_relations if force_relations else [],
        "entity_ids": entities,
        "relation_count": len(created_relations) if force_relations else 0,
    }


def to_dict(plan: DiffPlan) -> Dict[str, Any]:
    return {
        "repo": plan.repo,
        "base": plan.base,
        "head": plan.head,
        "files": [
            {
                "status": f.status,
                "path": f.path,
                "old_path": f.old_path,
                "added": f.added_lines,
                "removed": f.removed_lines,
                "risk_level": f.risk_level,
                "risk_reasons": f.risk_reasons,
            }
            for f in plan.files
        ],
        "targets": {name: ref.__dict__ for name, ref in plan.targets.items()},
        "relations": [r.__dict__ for r in plan.relations],
    }


def render_plan(plan: DiffPlan) -> str:
    lines = [
        f"# diff_to_onto dry-run",
        f"repo: `{plan.repo}`",
        f"range: `{plan.base}...{plan.head}`",
        f"files: {len(plan.files)}",
        f"targets: {len(plan.targets)}",
        f"relations: {len(plan.relations)}",
        "",
        "## Files",
    ]
    for change in plan.files:
        lines.append(
            f"- `{change.status}` `{change.path}` +{change.added_lines}/-{change.removed_lines} risk={change.risk_level}"
        )
        if change.risk_reasons:
            lines.append(f"  reasons: {', '.join(change.risk_reasons)}")
    lines.extend(["", "## Planned relations"])
    for relation in plan.relations:
        lines.append(
            f"- `{relation.relation_type}` `{relation.source_name}` → `{relation.target_name}`"
        )
    return "\n".join(lines)
