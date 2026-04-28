"""LangGraph state graph for the Phase B translation loop.

Wires all node functions into a compilable StateGraph. Two compiled graphs are
exposed:
- ``translation_graph``: no checkpointer, for direct CLI use (``vuemorphic phase-b``)
- ``build_checkpointed_graph(db_path)``: SqliteSaver checkpointer for ``vuemorphic serve``
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from vuemorphic.graph.nodes import (
    build_context,
    escalate_node,
    invoke_agent,
    pick_next_node,
    queue_for_review,
    requeue_node,
    retry_node,
    route_after_supervisor,
    route_after_verify,
    supervisor_node,
    transform_data_module,
    update_manifest,
    verify,
)
from vuemorphic.graph.state import VuemorphicState


def _route_pick(state: VuemorphicState) -> str:
    if state.get("done"):
        return "done"
    # DATA_MODULE nodes are transformed in code — skip the agent pipeline
    from vuemorphic.models.manifest import Manifest, NodeKind
    node_id = state.get("current_node_id")
    if node_id:
        try:
            manifest = Manifest.load(__import__("pathlib").Path(state["db_path"]))
            node = manifest.get_node(node_id)
            if node and node.node_kind == NodeKind.DATA_MODULE:
                return "data_module"
        except Exception:
            pass
    return "continue"


def build_graph(checkpointer=None) -> object:
    """Construct and compile the Phase B LangGraph state graph.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g. SqliteSaver).
                      Pass None for CLI usage; pass a SqliteSaver for ``vuemorphic serve``.
    """
    graph: StateGraph = StateGraph(VuemorphicState)

    graph.add_node("pick_next_node", pick_next_node)
    graph.add_node("build_context", build_context)
    graph.add_node("invoke_agent", invoke_agent)
    graph.add_node("verify", verify)
    graph.add_node("transform_data_module", transform_data_module)
    graph.add_node("retry_node", retry_node)
    graph.add_node("requeue_node", requeue_node)
    graph.add_node("escalate_node", escalate_node)
    graph.add_node("supervisor_node", supervisor_node)
    graph.add_node("update_manifest", update_manifest)
    graph.add_node("queue_for_review", queue_for_review)

    graph.set_entry_point("pick_next_node")

    graph.add_conditional_edges(
        "pick_next_node",
        _route_pick,
        {"continue": "build_context", "data_module": "transform_data_module", "done": END},
    )
    graph.add_edge("transform_data_module", "pick_next_node")
    graph.add_edge("build_context", "invoke_agent")
    graph.add_edge("invoke_agent", "verify")
    graph.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "update_manifest": "update_manifest",
            "retry": "retry_node",
            "requeue": "requeue_node",
            "escalate": "escalate_node",
            "supervisor": "supervisor_node",
            "queue_for_review": "queue_for_review",
        },
    )
    graph.add_edge("retry_node", "build_context")
    graph.add_edge("requeue_node", "pick_next_node")
    graph.add_edge("escalate_node", "build_context")
    graph.add_conditional_edges(
        "supervisor_node",
        route_after_supervisor,
        {
            "build_context": "build_context",
            "queue_for_review": "queue_for_review",
        },
    )
    graph.add_edge("update_manifest", "pick_next_node")
    graph.add_edge("queue_for_review", "pick_next_node")

    return graph.compile(checkpointer=checkpointer)


def build_checkpointed_graph(db_path: str) -> object:
    """Build a graph with SqliteSaver for use by ``vuemorphic serve``.

    Args:
        db_path: Absolute path to the SQLite checkpoint file (created if absent).
    """
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return build_graph(checkpointer=checkpointer)


# Compiled graph without checkpointer — used by ``vuemorphic phase-b`` CLI
translation_graph = build_graph()
