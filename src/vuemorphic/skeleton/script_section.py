"""Generate the <script setup lang="ts"> section of a Vue skeleton SFC."""

from __future__ import annotations
import re
from vuemorphic.models.contract import ComponentContract
from vuemorphic.skeleton.imports import (
    build_icon_import_line,
    build_shadcn_import_lines,
)


def _wrap_default(value: str) -> str:
    """Wrap object/array defaults in factory functions for Vue withDefaults.
    Skip complex expressions (dot access, function calls) — let the LLM handle those.
    """
    v = value.strip()
    # Simple literals: numbers, booleans, quoted strings → use as-is
    if re.match(r'^-?\d+(\.\d+)?$', v):  # number
        return v
    if v in ('true', 'false', 'null', 'undefined'):
        return v
    if v and v[0] in ('"', "'", '`'):  # string literal
        return v
    # Object/array literals → factory function
    if v.startswith(("{", "[")):
        return f"() => ({v})"
    # Anything else (e.g. wfColors.green, someVar) → omit from withDefaults
    # The LLM will supply the correct default from context
    return None  # type: ignore


def build_script(contract: ComponentContract) -> str:
    """Return the <script setup lang="ts"> block."""
    lines: list[str] = ['<script setup lang="ts">']

    # Vue framework imports
    vue_symbols = sorted(set(contract.vue_imports)) if contract.vue_imports else []
    if vue_symbols:
        lines.append(f"import {{ {', '.join(vue_symbols)} }} from 'vue'")
    else:
        lines.append("import { ref } from 'vue'")

    # Design token import
    if contract.references_design_tokens:
        lines.append("import { wfColors, wfFonts, mfColors, mfFonts } from '@/design-tokens'")

    # Icon imports
    if contract.icon_imports:
        icon_line = build_icon_import_line(contract.icon_imports)
        if icon_line:
            lines.append(icon_line)

    # shadcn-vue imports
    for shadcn_line in build_shadcn_import_lines(contract.shadcn_imports):
        lines.append(shadcn_line)

    lines.append("")

    # Props interface
    if contract.props_interface:
        lines.append(contract.props_interface)
        lines.append("")

        # defineProps
        if contract.prop_defaults:
            pairs = [
                f"{k}: {_wrap_default(v)}"
                for k, v in contract.prop_defaults.items()
                if _wrap_default(v) is not None
            ]
            if pairs:
                defaults_str = ", ".join(pairs)
                lines.append(
                    f"const props = withDefaults(defineProps<{contract.component_name}Props>(), "
                    f"{{ {defaults_str} }})"
                )
            else:
                lines.append(f"const props = defineProps<{contract.component_name}Props>()")
        else:
            lines.append(f"const props = defineProps<{contract.component_name}Props>()")

        lines.append("")

    # defineEmits
    if contract.emitted_events:
        emit_pairs = "; ".join(f"{e}: []" for e in contract.emitted_events)
        lines.append(f"const emit = defineEmits<{{ {emit_pairs} }}>()")
        lines.append("")

    lines.append(f"// TODO(vuemorphic): translate {contract.component_name} logic")
    lines.append("</script>")

    return "\n".join(lines)
