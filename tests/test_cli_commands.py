"""Tests for 'vuemorphic blocked' and 'vuemorphic escalate' CLI commands."""
from pathlib import Path

from typer.testing import CliRunner
from vuemorphic.cli import app
from vuemorphic.models.manifest import ConversionNode, NodeKind, NodeStatus, Manifest

runner = CliRunner()


def _make_blocked_manifest(tmp_path: Path) -> Path:
    db = tmp_path / "m.db"
    child = ConversionNode(
        node_id="FailedKid", source_file="f.jsx", line_start=1, line_end=5,
        source_text="const FailedKid = () => <div/>", node_kind=NodeKind.REACT_COMPONENT,
        status=NodeStatus.HUMAN_REVIEW,
        failure_category="complexity",
        failure_analysis="CATEGORY: complexity\nMISSING: nothing\nTRIED: all\nFIX: use sonnet",
    )
    parent = ConversionNode(
        node_id="WaitingParent", source_file="f.jsx", line_start=10, line_end=20,
        source_text="const WaitingParent = () => <FailedKid/>", node_kind=NodeKind.REACT_COMPONENT,
        call_dependencies=["FailedKid"],
    )
    Manifest(db, nodes={"FailedKid": child, "WaitingParent": parent})
    return db


def test_blocked_command_shows_human_review_nodes(tmp_path):
    db = _make_blocked_manifest(tmp_path)
    result = runner.invoke(app, ["blocked", "--db", str(db)])
    assert result.exit_code == 0
    assert "FailedKid" in result.output
    assert "WaitingParent" in result.output
    assert "complexity" in result.output


def test_blocked_command_shows_fix_lines(tmp_path):
    db = _make_blocked_manifest(tmp_path)
    result = runner.invoke(app, ["blocked", "--db", str(db)])
    assert "use sonnet" in result.output


def test_escalate_command_resets_node(tmp_path):
    db = _make_blocked_manifest(tmp_path)
    result = runner.invoke(app, ["escalate", "FailedKid", "--db", str(db), "--tier", "sonnet"])
    assert result.exit_code == 0
    manifest = Manifest.load(db)
    node = manifest.get_node("FailedKid")
    assert node.status == NodeStatus.NOT_STARTED
    assert node.tier.value == "sonnet"
    assert node.attempt_count == 0


def test_escalate_command_rejects_unknown_node(tmp_path):
    db = _make_blocked_manifest(tmp_path)
    result = runner.invoke(app, ["escalate", "DoesNotExist", "--db", str(db)])
    assert result.exit_code != 0 or "not found" in result.output.lower()
