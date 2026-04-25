"""Generate the <template> section of a Vue skeleton SFC."""

from __future__ import annotations
from vuemorphic.models.contract import ComponentContract


def build_template(contract: ComponentContract) -> str:
    """Return the <template> block with a TODO(vuemorphic) marker."""
    slot = "\n  <slot />" if contract.has_children_prop else ""
    return (
        f"<template>\n"
        f"  <!-- TODO(vuemorphic): translate {contract.component_name} template -->"
        f"{slot}\n"
        f"</template>"
    )
