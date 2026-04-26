"""Phase A.5: skeleton builder.

For each ComponentContract, generate a structurally valid .vue SFC with
TODO(vuemorphic): markers. The LLM fills in the markers in Phase B.

The skeleton gives vue-tsc a valid type envelope to check against before the
model touches anything — same purpose as vuemorphic's todo!() stubs, at Vue file
granularity instead of Rust function body granularity.
"""

from __future__ import annotations

import logging
from pathlib import Path

from vuemorphic.models.contract import ComponentContract
from vuemorphic.skeleton.template_section import build_template
from vuemorphic.skeleton.script_section import build_script
from vuemorphic.skeleton.style_section import build_style

logger = logging.getLogger(__name__)

# Marker that the verifier looks for as "model failed to fill in"
SKELETON_MARKER = "TODO(vuemorphic):"


def build_skeleton(contract: ComponentContract, target_dir: str) -> str:
    """Build and write the skeleton .vue file for a component.

    Args:
        contract:   ComponentContract from Phase A extraction.
        target_dir: Root of the generated Vue project (corpora/claude-design-vue).

    Returns:
        Absolute path to the written .vue file.
    """
    output_path = Path(target_dir) / "src" / "components" / f"{contract.component_name}.vue"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Never overwrite a converted file (one with no TODO markers remaining)
    if output_path.exists() and SKELETON_MARKER not in output_path.read_text(encoding="utf-8"):
        logger.debug("Skipping already-converted: %s", output_path)
        return str(output_path)

    template = build_template(contract)
    script = build_script(contract)
    style = build_style(contract)

    content = "\n\n".join([template, script, style]) + "\n"

    output_path.write_text(content, encoding="utf-8")
    logger.info("Wrote skeleton: %s (%d bytes)", output_path, len(content))
    return str(output_path)


def build_all_skeletons(
    contracts: list[ComponentContract], target_dir: str
) -> dict[str, str]:
    """Build skeletons for all contracts.

    Returns:
        Mapping from node_id → skeleton file path.
    """
    results: dict[str, str] = {}
    for contract in contracts:
        path = build_skeleton(contract, target_dir)
        results[contract.node_id] = path
    logger.info("Built %d skeletons in %s", len(results), target_dir)
    return results


def skeleton_is_unfilled(content: str) -> bool:
    """Return True if the file still contains any TODO(vuemorphic): markers."""
    return SKELETON_MARKER in content


def count_unfilled_markers(content: str) -> int:
    """Return the number of remaining TODO(vuemorphic): markers."""
    return content.count(SKELETON_MARKER)
