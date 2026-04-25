"""ComponentContract — per-component analysis record for the React→Vue pipeline."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ComponentContract:
    # Identity
    node_id: str
    """Unique id. Format: ComponentName, or stem.ComponentName on collision."""

    component_name: str
    """PascalCase component name as written in source."""

    source_file: str
    """Path relative to source_repo root, e.g. 'primitives.jsx'."""

    # Props
    props_interface: str
    """TypeScript interface text for defineProps.
    For .tsx: verbatim interface block. For plain .jsx: synthesised from defaults.
    Empty string when component takes no props."""

    prop_defaults: dict[str, str] = field(default_factory=dict)
    """prop name → default value string. Used for withDefaults()."""

    # Events
    emitted_events: list[str] = field(default_factory=list)
    """Emit names derived from callback props (onClose → 'close')."""

    # Dependencies
    child_components: list[str] = field(default_factory=list)
    """node_ids of components this one renders (PascalCase JSX tags)."""

    vue_imports: list[str] = field(default_factory=list)
    """Vue Composition API symbols needed (ref, computed, watch, onMounted...)."""

    icon_imports: list[str] = field(default_factory=list)
    """Icon component names from lucide-react (mapped to lucide-vue-next)."""

    shadcn_imports: list[str] = field(default_factory=list)
    """shadcn/ui component names (mapped to shadcn-vue paths)."""

    # Feature flags
    references_design_tokens: bool = False
    """True when body references wfColors, mfColors, wfFonts, or mfFonts."""

    has_children_prop: bool = False
    """True when component uses {children} → skeleton emits <slot />."""

    has_named_slots: bool = False
    """True when component uses render props or named children patterns."""
