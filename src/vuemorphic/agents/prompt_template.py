"""Conversion prompt template and renderer.

The template uses str.format() with named placeholders. All dynamic sections
are pre-assembled by build_prompt() in context.py before being passed here.
"""
from __future__ import annotations

# ── Static sections ────────────────────────────────────────────────────────────

_RULES = '''
## Rules
- IMPORTANT: Output the complete .vue file content directly in your response. Do NOT use file write tools.
- Fill in ALL TODO(vuemorphic): markers — any leftover marker is a verification failure
- Do NOT add // TODO or // FIXME comments — post-filter will reject them
- Do NOT leave any React idioms: no className=, no import React, no useState, no JSX.Element
- Use the composition API with <script setup lang="ts"> — no Options API
- Translate semantically faithfully — match every branch in the React source
- Props interface comes from the skeleton — you MAY narrow `any` types to something more specific if the React source makes the shape clear, but do not remove or rename props
- Use defineProps/defineEmits exactly as scaffolded in the skeleton
- Approved packages: {packages}
'''

_OUTPUT_FORMAT = '''
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
Renders the sidebar navigation. Accepts items array and emits select event on click.
'''

_BLOCKED_FORM = '''
## If You Cannot Complete This Conversion
If the Vue output you produce cannot pass verification, fill in the section below after your ---SUMMARY--- line.
Use exactly this format (one line per key, no extra blank lines between keys):

---BLOCKED---
CATEGORY: [choose one: info_gap | prompt_confusion | tooling | complexity | cascade | unknown]
MISSING:  [what specific information or capability was absent — be concrete]
TRIED:    [what approaches you attempted before giving up]
FIX:      [one concrete change to the harness, prompt, or context that would have helped]
'''

# ── Template ───────────────────────────────────────────────────────────────────

CONVERSION_PROMPT = '''
You are converting one React JSX component to a Vue 3 SFC as part of porting Claude Design artifacts.

## Your job
1. Read the React source to understand the component's logic and structure
2. Read the Vue skeleton — it has the correct <script setup> envelope and TODO(vuemorphic): markers
3. Fill in every TODO(vuemorphic): marker — do NOT leave any behind
4. Output the complete .vue file directly in your response (no file tools, no markdown fences)

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
{rules}
## Architectural Decisions
{arch_decisions}
{deps_section}{transitive_section}{idiom_section}{supervisor_section}{retry_section}{unfurl_section}
{output_format}
{blocked_form}
'''.strip()


# ── Renderer ───────────────────────────────────────────────────────────────────

def render_prompt(
    *,
    node_id: str,
    jsx_source_path: str,
    vue_skeleton_path: str,
    vue_output_path: str,
    line_start: int,
    line_end: int,
    source_text: str,
    skeleton_text: str,
    packages: str,
    arch_decisions: str,
    deps_section: str,
    transitive_section: str,
    idiom_section: str,
    supervisor_section: str,
    retry_section: str,
    unfurl_section: str,
) -> str:
    rules = _RULES.format(packages=packages)
    return CONVERSION_PROMPT.format(
        node_id=node_id,
        jsx_source_path=jsx_source_path,
        vue_skeleton_path=vue_skeleton_path,
        vue_output_path=vue_output_path,
        line_start=line_start,
        line_end=line_end,
        source_text=source_text,
        skeleton_text=skeleton_text,
        rules=rules,
        arch_decisions=arch_decisions,
        deps_section=deps_section,
        transitive_section=transitive_section,
        idiom_section=idiom_section,
        supervisor_section=supervisor_section,
        retry_section=retry_section,
        unfurl_section=unfurl_section,
        output_format=_OUTPUT_FORMAT,
        blocked_form=_BLOCKED_FORM,
    )
