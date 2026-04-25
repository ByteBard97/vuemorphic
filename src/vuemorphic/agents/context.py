"""Assemble the conversion prompt for a single manifest node.

The prompt includes: React JSX source, Vue skeleton from Phase A.5, converted
dependency .vue files, idiom dictionary entries, and retry context.

Progressive Context Disclosure levels:
  Level 1 (always): Full <script setup> + <template> for direct dep components
  Level 2 (always): Truncated (20 lines) for transitive (2-hop) deps
  JIT Unfurl (retry only): Full .vue files for components named in vue-tsc errors
"""
from __future__ import annotations

import re
from pathlib import Path

from vuemorphic.models.manifest import ConversionNode, Manifest

_PROMPT_TEMPLATE = """\
You are converting one React JSX component to a Vue 3 SFC as part of porting Claude Design artifacts.

## Your job
1. Read the React source to understand the component's logic and structure
2. Read the Vue skeleton — it has the correct <script setup> envelope and TODO(vuemorphic): markers
3. Fill in every TODO(vuemorphic): marker — do NOT leave any behind
4. The output must be the complete .vue file with no markdown fences, no explanation

## Files
- React source: {jsx_source_path} (lines {line_start}--{line_end})
- Vue skeleton (your starting point): {vue_skeleton_path}
- Output .vue path: {vue_output_path}
- Component: `{node_id}`

## React Source
```jsx
{source_text}
```

## Vue Skeleton
```vue
{skeleton_text}
```

## Rules
- Fill in ALL TODO(vuemorphic): markers — any leftover marker is a verification failure
- Do NOT add // TODO or // FIXME comments — post-filter will reject them
- Do NOT leave any React idioms: no className=, no import React, no useState, no JSX.Element
- Use the composition API with <script setup lang="ts"> — no Options API
- Translate semantically faithfully — match every branch in the React source
- Props interface comes from the skeleton — do not change it
- Use defineProps/defineEmits exactly as scaffolded in the skeleton
- Approved packages: {packages}

## Architectural Decisions
{arch_decisions}
{deps_section}\
{transitive_section}\
{idiom_section}\
{supervisor_section}\
{retry_section}\
{unfurl_section}\
Output two things separated by the literal line `---SUMMARY---`:
1. The complete .vue file content (no markdown fences, no explanation)
2. 1-2 sentences describing what this component does (used as context for callers)

Example format:
<template>
  <div class="sidebar">...</div>
</template>

<script setup lang="ts">
...
</script>
---SUMMARY---
Renders the sidebar navigation. Accepts items array and emits select event on click.\
"""


def _load_dep_snippets(
    node: ConversionNode,
    manifest: Manifest,
    snippets_dir: Path,
) -> tuple[str, set[str]]:
    """Load converted .vue content for direct (1-hop) in-manifest dependencies.

    Returns (snippet_text, direct_dep_ids) so the caller can avoid double-loading
    them in the transitive pass.
    """
    lines: list[str] = []
    seen: set[str] = set()
    direct_dep_ids: set[str] = set(node.type_dependencies) | set(node.call_dependencies)

    all_nodes = manifest.nodes
    for dep_id in list(node.type_dependencies) + list(node.call_dependencies):
        if dep_id in seen or dep_id not in all_nodes:
            continue
        seen.add(dep_id)
        dep_node = all_nodes[dep_id]
        if not dep_node.snippet_path:
            continue
        p = Path(dep_node.snippet_path)
        if p.exists():
            lines.append(f"<!-- -- {dep_id} -- -->")
            lines.append(p.read_text().strip())

    return "\n".join(lines), direct_dep_ids


# ── Transitive deps (Level 2): truncated for 2-hop neighbors ──────────────────

_TRANSITIVE_TRUNCATE_LINES = 20
_TRANSITIVE_CHAR_BUDGET = 2000


def _load_transitive_dep_snippets(
    node: ConversionNode,
    manifest: Manifest,
    direct_dep_ids: set[str],
) -> str:
    """Load first 20 lines of converted .vue files for 2-hop transitive deps."""
    all_nodes = manifest.nodes
    seen: set[str] = set(direct_dep_ids) | {node.node_id}
    transitive: list[str] = []

    for dep_id in direct_dep_ids:
        dep_node = all_nodes.get(dep_id)
        if dep_node is None:
            continue
        for tdep in list(dep_node.type_dependencies) + list(dep_node.call_dependencies):
            if tdep not in seen and tdep in all_nodes:
                transitive.append(tdep)
                seen.add(tdep)

    if not transitive:
        return ""

    parts: list[str] = []
    remaining = _TRANSITIVE_CHAR_BUDGET

    for dep_id in transitive:
        dep_node = all_nodes.get(dep_id)
        if dep_node is None:
            continue
        if dep_node.summary_text:
            entry = f"<!-- -- {dep_id} (transitive) -->\n<!-- {dep_node.summary_text} -->"
        elif dep_node.snippet_path:
            p = Path(dep_node.snippet_path)
            if not p.exists():
                continue
            snippet_lines = p.read_text().strip().splitlines()
            truncated = "\n".join(snippet_lines[:_TRANSITIVE_TRUNCATE_LINES])
            if len(snippet_lines) > _TRANSITIVE_TRUNCATE_LINES:
                truncated += f"\n<!-- ... ({len(snippet_lines) - _TRANSITIVE_TRUNCATE_LINES} more lines) -->"
            entry = f"<!-- -- {dep_id} (transitive) -->\n{truncated}"
        else:
            continue
        if remaining - len(entry) < 0:
            parts.append(f"<!-- [transitive budget exhausted — {remaining} chars remaining] -->")
            break
        parts.append(entry)
        remaining -= len(entry)

    return "\n\n".join(parts)


# ── JIT Unfurling (Level 2 retry): load .vue files named in vue-tsc errors ────

_VUETSC_FILE_RE = re.compile(r"^(src/components/[\w/]+\.vue)\(\d+,\d+\)", re.MULTILINE)
_UNFURL_CHAR_BUDGET = 3000


def _parse_error_components(error_text: str, target_vue_filename: str) -> list[str]:
    """Extract unique component names from vue-tsc error lines.

    Excludes the target file — those errors are for the agent to fix.
    """
    files = set(_VUETSC_FILE_RE.findall(error_text))
    files.discard(target_vue_filename)
    components: list[str] = []
    for f in sorted(files):
        stem = Path(f).stem
        if stem not in components:
            components.append(stem)
    return components


def _load_component_snippets(
    component_name: str,
    manifest: Manifest,
    char_budget: int,
) -> str:
    """Load converted .vue content for nodes whose node_id matches component_name."""
    lines: list[str] = []
    total_chars = 0

    for node_id, node in manifest.nodes.items():
        if node_id != component_name and not node_id.endswith(f"/{component_name}"):
            continue
        if not node.snippet_path:
            continue
        p = Path(node.snippet_path)
        if not p.exists():
            continue
        body = p.read_text().strip()
        entry = f"<!-- -- {node_id} -->\n{body}"
        if total_chars + len(entry) > char_budget:
            lines.append(f"<!-- [truncated: budget exhausted at {total_chars} chars] -->")
            break
        lines.append(entry)
        total_chars += len(entry)

    return "\n\n".join(lines)


def _load_unfurled_deps(
    error_text: str,
    target_vue_filename: str,
    manifest: Manifest,
) -> str:
    """JIT unfurl: given a vue-tsc error, load .vue files for all implicated components."""
    components = _parse_error_components(error_text, target_vue_filename)
    if not components:
        return ""

    parts: list[str] = []
    remaining_budget = _UNFURL_CHAR_BUDGET

    for component in components:
        content = _load_component_snippets(component, manifest, remaining_budget)
        if content:
            parts.append(f"<!-- === Component: {component} ===\n{content}")
            remaining_budget -= len(content)
        if remaining_budget <= 0:
            break

    return "\n\n".join(parts)


def _load_idiom_entries(idioms: list[str], workspace: Path, references_design_tokens: bool = False) -> str:
    """Load relevant sections from idiom_dictionary.md for the node's idioms."""
    dict_path = workspace / "idiom_dictionary.md"
    if not dict_path.exists():
        return ""

    # Always include claude_design_globals when component references design tokens
    all_idioms = list(idioms)
    if references_design_tokens and "claude_design_globals" not in all_idioms:
        all_idioms.append("claude_design_globals")

    if not all_idioms:
        return ""

    content = dict_path.read_text()
    entries: list[str] = []
    for idiom in all_idioms:
        pattern = re.compile(
            rf"^##\s+{re.escape(idiom)}\b.*?(?=^##\s|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        m = pattern.search(content)
        if m:
            entries.append(m.group(0).strip())

    return "\n\n".join(entries)


def _read_skeleton(node_id: str, target_vue_path: Path) -> str:
    """Read the Phase A.5 skeleton .vue file for this component."""
    vue_file = target_vue_path / "src" / "components" / f"{node_id}.vue"
    if not vue_file.exists():
        return f"<!-- skeleton not found: {node_id}.vue -->"
    return vue_file.read_text()


def build_prompt(
    node: ConversionNode,
    manifest: Manifest,
    config: dict,
    target_vue_path: Path,
    snippets_dir: Path,
    workspace: Path,
    last_error: str | None = None,
    attempt_count: int = 0,
    supervisor_hint: str | None = None,
) -> str:
    """Build the full conversion prompt for one manifest node."""
    packages = ", ".join(config.get("crate_inventory", []))
    arch = config.get("architectural_decisions", {})
    arch_lines = "\n".join(f"- {k}: {v}" for k, v in arch.items()) or "None specified."

    skeleton_text = _read_skeleton(node.node_id, target_vue_path)

    # Level 1: direct dep snippets
    dep_text, direct_dep_ids = _load_dep_snippets(node, manifest, snippets_dir)
    deps_section = (
        f"\n## Converted Dependencies\n```vue\n{dep_text}\n```\n"
        if dep_text
        else ""
    )

    # Level 2: transitive (2-hop) dep snippets, truncated
    transitive_text = _load_transitive_dep_snippets(node, manifest, direct_dep_ids)
    transitive_section = (
        f"\n## Transitive Dependencies (truncated to {_TRANSITIVE_TRUNCATE_LINES} lines each)\n"
        f"```vue\n{transitive_text}\n```\n"
        if transitive_text
        else ""
    )

    references_design_tokens = getattr(node, "references_design_tokens", False)
    idiom_text = _load_idiom_entries(node.idioms_needed, workspace, references_design_tokens)
    idiom_section = f"\n## Idiom Translations\n{idiom_text}\n" if idiom_text else ""

    retry_section = ""
    if attempt_count > 0 and last_error:
        retry_section = (
            f"\n## Previous Attempt Failed (attempt {attempt_count})\n"
            f"Fix this error:\n```\n{last_error}\n```\n"
        )

    # JIT Unfurling: on retry, load full .vue files for components named in the error
    unfurl_section = ""
    if attempt_count > 0 and last_error:
        target_vue_filename = f"src/components/{node.node_id}.vue"
        unfurled_text = _load_unfurled_deps(last_error, target_vue_filename, manifest)
        if unfurled_text:
            unfurl_section = (
                f"\n## Unfurled Dependencies (from vue-tsc errors)\n"
                f"Implementations of components mentioned in the previous error:\n"
                f"```vue\n{unfurled_text}\n```\n"
            )

    supervisor_section = ""
    if supervisor_hint:
        supervisor_section = (
            f"\n## Supervisor Hint\n"
            f"A supervisor agent has reviewed previous failures and suggests:\n"
            f"{supervisor_hint}\n"
        )

    source_repo = config.get("source_repo", "corpora/claude-design-react")
    jsx_source_path = (workspace / source_repo / node.source_file).resolve()
    vue_skeleton_path = (target_vue_path / "src" / "components" / f"{node.node_id}.vue").resolve()
    vue_output_path = vue_skeleton_path  # same file — agent fills in the skeleton

    return _PROMPT_TEMPLATE.format(
        packages=packages,
        arch_decisions=arch_lines,
        node_id=node.node_id,
        jsx_source_path=jsx_source_path,
        vue_skeleton_path=vue_skeleton_path,
        vue_output_path=vue_output_path,
        line_start=node.line_start,
        line_end=node.line_end,
        source_text=node.source_text,
        skeleton_text=skeleton_text,
        deps_section=deps_section,
        transitive_section=transitive_section,
        idiom_section=idiom_section,
        supervisor_section=supervisor_section,
        retry_section=retry_section,
        unfurl_section=unfurl_section,
    )
