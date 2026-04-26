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


# ── Task 5: parse ---BLOCKED--- in invoke_agent ───────────────────────────────

def test_invoke_agent_strips_blocked_form_from_vue_content(tmp_path, monkeypatch):
    """---BLOCKED--- and everything after it is stripped from current_vue_content."""
    from vuemorphic.graph.nodes import invoke_agent
    from vuemorphic.models.manifest import ConversionNode, NodeKind, Manifest

    raw_response = (
        "<template><div>hello</div></template>\n"
        "<script setup lang='ts'>\n</script>\n"
        "---SUMMARY---\nDoes a thing.\n"
        "---BLOCKED---\n"
        "CATEGORY: info_gap\nMISSING: prop shape\nTRIED: guessing\nFIX: inject source\n"
    )
    monkeypatch.setattr("vuemorphic.graph.nodes.invoke_claude", lambda **kw: raw_response)
    db = tmp_path / "m.db"
    node = ConversionNode(
        node_id="Foo", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const Foo = () => <div/>", node_kind=NodeKind.REACT_COMPONENT,
    )
    Manifest(db, nodes={"Foo": node})

    state = base_vuemorphic_state(str(db), current_node_id="Foo", current_tier="haiku")
    result = invoke_agent(state)

    assert "---BLOCKED---" not in result["current_vue_content"]
    assert "CATEGORY: info_gap" in result["failure_analysis"]


def test_invoke_agent_failure_analysis_none_when_no_blocked_form(tmp_path, monkeypatch):
    """failure_analysis is None when the agent response has no ---BLOCKED--- section."""
    from vuemorphic.graph.nodes import invoke_agent
    from vuemorphic.models.manifest import ConversionNode, NodeKind, Manifest

    raw_response = "<template><div/></template>\n<script setup lang='ts'></script>\n---SUMMARY---\nDoes a thing."
    monkeypatch.setattr("vuemorphic.graph.nodes.invoke_claude", lambda **kw: raw_response)
    db = tmp_path / "m.db"
    node = ConversionNode(
        node_id="Bar", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const Bar = () => <div/>", node_kind=NodeKind.REACT_COMPONENT,
    )
    Manifest(db, nodes={"Bar": node})
    state = base_vuemorphic_state(str(db), current_node_id="Bar", current_tier="haiku")
    result = invoke_agent(state)
    assert result.get("failure_analysis") is None


def test_queue_for_review_stores_failure_analysis(tmp_path):
    """queue_for_review writes failure_category and failure_analysis to the DB."""
    from vuemorphic.graph.nodes import queue_for_review
    from vuemorphic.models.manifest import ConversionNode, NodeKind, Manifest, NodeStatus

    db = tmp_path / "m.db"
    node = ConversionNode(
        node_id="Baz", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const Baz = () => <div/>", node_kind=NodeKind.REACT_COMPONENT,
    )
    Manifest(db, nodes={"Baz": node})

    analysis = "CATEGORY: complexity\nMISSING: nothing\nTRIED: all\nFIX: use sonnet"
    state = base_vuemorphic_state(
        str(db), current_node_id="Baz", current_tier="haiku",
        attempt_count=3, last_error="vue-tsc failed",
        failure_analysis=analysis,
    )
    queue_for_review(state)

    manifest = Manifest.load(db)
    loaded = manifest.get_node("Baz")
    assert loaded.status == NodeStatus.HUMAN_REVIEW
    assert loaded.failure_category == "complexity"
    assert "complexity" in loaded.failure_analysis
