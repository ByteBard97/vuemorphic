"""Tests for failure diagnosis features — avoids stale oxidant imports in test_graph_nodes.py."""
import logging

import pytest

from tests.conftest import base_vuemorphic_state


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def minimal_node():
    from vuemorphic.models.manifest import ConversionNode, NodeKind
    return ConversionNode(
        node_id="TestComp", source_file="test.jsx", line_start=1, line_end=10,
        source_text="const TestComp = ({ label }) => <div>{label}</div>",
        node_kind=NodeKind.REACT_COMPONENT,
    )


@pytest.fixture
def minimal_manifest(tmp_path):
    from vuemorphic.models.manifest import Manifest
    db = tmp_path / "manifest.db"
    return Manifest(db)


def test_pick_next_node_logs_blocked_not_all_converted(tmp_path, caplog):
    """When claim returns None due to blockers, log says 'blocked' not 'all converted'."""
    from vuemorphic.graph.nodes import pick_next_node
    from vuemorphic.models.manifest import ConversionNode, NodeKind, NodeStatus, Manifest

    db = tmp_path / "m.db"
    child = ConversionNode(
        node_id="BlockedKid", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const BlockedKid = () => <div/>", node_kind=NodeKind.REACT_COMPONENT,
        status=NodeStatus.HUMAN_REVIEW,
    )
    parent = ConversionNode(
        node_id="WaitingDad", source_file="f.jsx", line_start=10, line_end=20,
        source_text="const WaitingDad = () => <BlockedKid/>", node_kind=NodeKind.REACT_COMPONENT,
        call_dependencies=["BlockedKid"],
    )
    Manifest(db, nodes={"BlockedKid": child, "WaitingDad": parent})

    state = base_vuemorphic_state(str(db))
    with caplog.at_level(logging.WARNING):
        result = pick_next_node(state)

    assert result["done"] is True
    assert "blocked" in caplog.text.lower() or "BlockedKid" in caplog.text
    assert "all nodes converted" not in caplog.text.lower()


# ── Task 4: ---BLOCKED--- form in prompts ─────────────────────────────────────

def test_prompt_always_contains_blocked_form(minimal_node, minimal_manifest, tmp_path):
    """Every generated prompt must contain the ---BLOCKED--- form template."""
    from vuemorphic.agents.context import build_prompt
    prompt = build_prompt(
        node=minimal_node,
        manifest=minimal_manifest,
        config={"package_inventory": [], "architectural_decisions": {}},
        target_vue_path=tmp_path,
        snippets_dir=tmp_path / "snippets",
        workspace=tmp_path,
    )
    assert "---BLOCKED---" in prompt
    assert "CATEGORY:" in prompt
    assert "MISSING:" in prompt
    assert "FIX:" in prompt


def test_retry_prompt_includes_previous_failure_analysis(minimal_node, minimal_manifest, tmp_path):
    """On retry, if previous_failure_analysis is set, it appears before the form with a meta-question."""
    from vuemorphic.agents.context import build_prompt
    prev_analysis = "CATEGORY: info_gap\nMISSING: prop shape of DCFoo\nFIX: inject source"
    prompt = build_prompt(
        node=minimal_node,
        manifest=minimal_manifest,
        config={"package_inventory": [], "architectural_decisions": {}},
        target_vue_path=tmp_path,
        snippets_dir=tmp_path / "snippets",
        workspace=tmp_path,
        last_error="vue-tsc: Property x does not exist",
        attempt_count=1,
        previous_failure_analysis=prev_analysis,
    )
    assert "CATEGORY: info_gap" in prompt
    assert "Is this diagnosis correct" in prompt
