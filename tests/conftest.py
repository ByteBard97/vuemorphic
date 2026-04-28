import pytest


def base_vuemorphic_state(db_path: str, **kw) -> dict:
    defaults = {
        "db_path": db_path,
        "target_vue_path": "/nonexistent",
        "snippets_dir": "/tmp/snippets",
        "config": {"crate_inventory": [], "architectural_decisions": {}, "model_tiers": {}, "package_inventory": []},
        "worker_id": 0,
        "current_node_id": None,
        "current_prompt": None,
        "current_vue_content": None,
        "current_raw_response": None,
        "current_tier": None,
        "attempt_count": 0,
        "last_error": None,
        "verify_status": None,
        "review_queue": [],
        "done": False,
        "max_nodes": None,
        "nodes_this_run": 0,
        "supervisor_hint": None,
        "interrupt_payload": None,
        "review_mode": "auto",
        "failure_analysis": None,
        "cascade_count": 0,
    }
    defaults.update(kw)
    return defaults
