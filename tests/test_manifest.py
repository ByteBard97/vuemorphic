import pytest
from pathlib import Path
from vuemorphic.models.manifest import (
    ConversionNode, Manifest, NodeKind, NodeStatus, TranslationTier
)


def test_node_roundtrip():
    node = ConversionNode(
        node_id="simple__Point__add",
        source_file="simple.ts",
        line_start=8,
        line_end=10,
        source_text="add(other: Point): Point { return new Point(this.x + other.x, this.y + other.y); }",
        node_kind=NodeKind.METHOD,
        parameter_types={"other": "Point"},
        return_type="Point",
        type_dependencies=["simple__Point"],
        call_dependencies=[],
        callers=["simple__distance"],
        parent_class="simple__Point",
    )
    assert node.status == NodeStatus.NOT_STARTED
    assert node.tier is None
    assert node.snippet_path is None
    data = node.model_dump()
    node2 = ConversionNode(**data)
    assert node2.node_id == node.node_id


def _make_manifest(nodes: dict) -> Manifest:
    """Create an in-memory manifest for testing."""
    return Manifest(nodes=nodes, source_repo=".", generated_at="2026-04-15")


def test_manifest_eligible_nodes_respects_deps():
    nodes = {
        "mod__A": ConversionNode(
            node_id="mod__A", source_file="a.ts", line_start=1, line_end=5,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type="void",
            type_dependencies=[], call_dependencies=[], callers=["mod__B"],
        ),
        "mod__B": ConversionNode(
            node_id="mod__B", source_file="a.ts", line_start=7, line_end=12,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type="void",
            type_dependencies=[], call_dependencies=["mod__A"], callers=[],
        ),
    }
    manifest = _make_manifest(nodes)
    eligible = manifest.eligible_nodes()
    assert len(eligible) == 1
    assert eligible[0].node_id == "mod__A"


def test_manifest_persistence(tmp_path):
    """Nodes written to a file-backed manifest are readable after Manifest.load()."""
    db_path = tmp_path / "test.db"
    nodes = {
        "mod__foo": ConversionNode(
            node_id="mod__foo", source_file="a.ts", line_start=1, line_end=3,
            source_text="function foo() {}", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type="void",
            type_dependencies=[], call_dependencies=[], callers=[],
        )
    }
    Manifest(db_path, source_repo=".", generated_at="2026-04-15", nodes=nodes)
    loaded = Manifest.load(db_path)
    assert loaded.nodes["mod__foo"].source_text == "function foo() {}"


def test_topological_sort_chain():
    # A → B → C (A has no deps; C depends on B; B depends on A)
    nodes = {
        "m__A": ConversionNode(
            node_id="m__A", source_file="x.ts", line_start=1, line_end=2,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type=None,
            type_dependencies=[], call_dependencies=[], callers=["m__B"],
        ),
        "m__B": ConversionNode(
            node_id="m__B", source_file="x.ts", line_start=3, line_end=4,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type=None,
            type_dependencies=[], call_dependencies=["m__A"], callers=["m__C"],
        ),
        "m__C": ConversionNode(
            node_id="m__C", source_file="x.ts", line_start=5, line_end=6,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type=None,
            type_dependencies=["m__B"], call_dependencies=[], callers=[],
        ),
    }
    manifest = _make_manifest(nodes)
    manifest.compute_topology()
    assert manifest.nodes["m__A"].topological_order == 0
    assert manifest.nodes["m__B"].topological_order == 1
    assert manifest.nodes["m__C"].topological_order == 2
    assert manifest.nodes["m__A"].bfs_level == 0
    assert manifest.nodes["m__B"].bfs_level == 1
    assert manifest.nodes["m__C"].bfs_level == 2


def test_topological_sort_parallel_nodes():
    # A and B are both leaves; C depends on both → A and B are level 0, C is level 1
    nodes = {
        "m__A": ConversionNode(
            node_id="m__A", source_file="x.ts", line_start=1, line_end=2,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type=None,
            type_dependencies=[], call_dependencies=[], callers=["m__C"],
        ),
        "m__B": ConversionNode(
            node_id="m__B", source_file="x.ts", line_start=3, line_end=4,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type=None,
            type_dependencies=[], call_dependencies=[], callers=["m__C"],
        ),
        "m__C": ConversionNode(
            node_id="m__C", source_file="x.ts", line_start=5, line_end=6,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type=None,
            type_dependencies=["m__A", "m__B"], call_dependencies=[], callers=[],
        ),
    }
    manifest = _make_manifest(nodes)
    manifest.compute_topology()
    assert manifest.nodes["m__A"].bfs_level == 0
    assert manifest.nodes["m__B"].bfs_level == 0
    assert manifest.nodes["m__C"].bfs_level == 1


def test_conversion_node_has_failure_fields():
    node = ConversionNode(
        node_id="Foo", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const Foo = () => <div/>",
        node_kind=NodeKind.REACT_COMPONENT,
    )
    assert node.failure_category is None
    assert node.failure_analysis is None


def test_node_record_roundtrip_failure_fields(tmp_path):
    db = tmp_path / "test.db"
    node = ConversionNode(
        node_id="Bar", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const Bar = () => <div/>",
        node_kind=NodeKind.REACT_COMPONENT,
        failure_category="info_gap",
        failure_analysis="CATEGORY: info_gap\nMISSING: prop shape of DCFoo\nFIX: inject unconverted dep source",
    )
    manifest = Manifest(db, nodes={"Bar": node})
    loaded = manifest.get_node("Bar")
    assert loaded.failure_category == "info_gap"
    assert "info_gap" in loaded.failure_analysis


def test_claim_next_eligible_hard_stops_when_child_in_human_review(tmp_path):
    """When all not-started nodes have unconverted deps, claim returns None (hard stop)."""
    db = tmp_path / "test.db"
    child = ConversionNode(
        node_id="Child", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const Child = () => <div/>", node_kind=NodeKind.REACT_COMPONENT,
        status=NodeStatus.HUMAN_REVIEW,
    )
    parent = ConversionNode(
        node_id="Parent", source_file="f.jsx", line_start=10, line_end=20,
        source_text="const Parent = () => <Child/>", node_kind=NodeKind.REACT_COMPONENT,
        call_dependencies=["Child"],
    )
    manifest = Manifest(db, nodes={"Child": child, "Parent": parent})
    result = manifest.claim_next_eligible()
    assert result is None


def test_claim_next_eligible_returns_none_reports_blockers(tmp_path, caplog):
    """When claim returns None due to blockers, logs which nodes are blocking."""
    import logging
    db = tmp_path / "test.db"
    child = ConversionNode(
        node_id="BlockedChild", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const BlockedChild = () => <div/>", node_kind=NodeKind.REACT_COMPONENT,
        status=NodeStatus.HUMAN_REVIEW,
    )
    parent = ConversionNode(
        node_id="WaitingParent", source_file="f.jsx", line_start=10, line_end=20,
        source_text="const WaitingParent = () => <BlockedChild/>", node_kind=NodeKind.REACT_COMPONENT,
        call_dependencies=["BlockedChild"],
    )
    manifest = Manifest(db, nodes={"BlockedChild": child, "WaitingParent": parent})
    with caplog.at_level(logging.WARNING):
        result = manifest.claim_next_eligible()
    assert result is None
    assert "BlockedChild" in caplog.text


def test_blocked_report(tmp_path):
    """blocked_report returns dict mapping human_review node_ids to lists of waiting node_ids."""
    db = tmp_path / "test.db"
    child = ConversionNode(
        node_id="FailedChild", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const FailedChild = () => <div/>", node_kind=NodeKind.REACT_COMPONENT,
        status=NodeStatus.HUMAN_REVIEW,
        failure_category="complexity",
        failure_analysis="CATEGORY: complexity\nMISSING: nothing\nFIX: needs sonnet",
    )
    parent = ConversionNode(
        node_id="StuckParent", source_file="f.jsx", line_start=10, line_end=20,
        source_text="const StuckParent = () => <FailedChild/>", node_kind=NodeKind.REACT_COMPONENT,
        call_dependencies=["FailedChild"],
    )
    manifest = Manifest(db, nodes={"FailedChild": child, "StuckParent": parent})
    report = manifest.blocked_report()
    assert "FailedChild" in report
    assert "StuckParent" in report["FailedChild"]["waiting"]
    assert report["FailedChild"]["failure_category"] == "complexity"


def test_topological_sort_cycle_gets_fallback_order():
    """Cycles don't raise — both nodes get distinct fallback topological orders."""
    nodes = {
        "m__A": ConversionNode(
            node_id="m__A", source_file="x.ts", line_start=1, line_end=2,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type=None,
            type_dependencies=["m__B"], call_dependencies=[], callers=[],
        ),
        "m__B": ConversionNode(
            node_id="m__B", source_file="x.ts", line_start=3, line_end=4,
            source_text="", node_kind=NodeKind.FREE_FUNCTION,
            parameter_types={}, return_type=None,
            type_dependencies=["m__A"], call_dependencies=[], callers=[],
        ),
    }
    manifest = _make_manifest(nodes)
    manifest.compute_topology()  # must not raise
    result = manifest.nodes
    assert result["m__A"].topological_order is not None
    assert result["m__B"].topological_order is not None
    assert result["m__A"].topological_order != result["m__B"].topological_order
