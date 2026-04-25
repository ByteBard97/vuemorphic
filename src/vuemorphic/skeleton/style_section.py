"""Generate the <style scoped> section of a Vue skeleton SFC."""

from __future__ import annotations
from vuemorphic.models.contract import ComponentContract


def build_style(contract: ComponentContract) -> str:
    """Return a scoped style block with a TODO(vuemorphic) marker."""
    return (
        f"<style scoped>\n"
        f"/* TODO(vuemorphic): translate {contract.component_name} styles if needed */\n"
        f"</style>"
    )
