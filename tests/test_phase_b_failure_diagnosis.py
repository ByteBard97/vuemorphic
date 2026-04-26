"""Tests for failure diagnosis features — avoids stale oxidant imports in test_graph_nodes.py."""
import logging

from tests.conftest import base_vuemorphic_state


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
