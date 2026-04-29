"""LangGraph node functions for the Phase B translation loop.

Each function receives the full VuemorphicState and returns ONLY the keys it modifies.
Never return {**state, ...} — that would cause the operator.add reducer on
review_queue to double-accumulate existing entries.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from vuemorphic.agents.context import build_prompt
from vuemorphic.agents.invoke import invoke_claude, invoke_ollama, invoke_anthropic_api, invoke_pi
from vuemorphic.graph.state import VuemorphicState
from vuemorphic.models.manifest import Manifest, NodeStatus, TranslationTier
from vuemorphic.verification.verify import VerifyStatus, verify_vue_file

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS: dict[str, int] = {"haiku": 3, "sonnet": 4, "opus": 5}
_DEFAULT_MAX_ATTEMPTS = 3

_BLOCKED_DELIMITER = "---BLOCKED---"
_CATEGORY_RE = re.compile(r"^CATEGORY:\s*(\S+)", re.MULTILINE)


def _db(state: VuemorphicState) -> Path:
    return Path(state["db_path"])


def pick_next_node(state: VuemorphicState) -> dict:
    """Atomically claim the next eligible node, or signal done."""
    max_nodes = state.get("max_nodes")
    nodes_this_run = state.get("nodes_this_run", 0)
    if max_nodes is not None and nodes_this_run >= max_nodes:
        logger.info("Reached --max-nodes limit (%d). Stopping.", max_nodes)
        return {"current_node_id": None, "done": True}

    complexity_max = state.get("config", {}).get("complexity_max")
    manifest = Manifest.load(_db(state))
    parallelism = state.get("config", {}).get("parallelism", 1)

    if parallelism <= 1:
        stuck = [
            nid for nid, n in manifest.nodes.items()
            if n.status == NodeStatus.IN_PROGRESS
            and nid != state.get("current_node_id")
        ]
        if stuck:
            logger.warning("Resetting %d orphaned in_progress nodes: %s", len(stuck), stuck[:3])
            for nid in stuck:
                manifest.update_node(_db(state), nid, status=NodeStatus.NOT_STARTED)

    node = manifest.claim_next_eligible(complexity_max=complexity_max)

    if node is None:
        # Distinguish: are we truly done, or hard-stopped by blocked nodes?
        any_not_started = any(
            n.status == NodeStatus.NOT_STARTED for n in manifest.nodes.values()
        )
        if any_not_started:
            logger.warning("Phase B hard-stopped: remaining nodes are blocked by unconverted deps.")
        else:
            logger.info("All nodes converted or queued. Phase B complete.")
        return {"current_node_id": None, "done": True}

    available_tiers = set(state.get("config", {}).get("model_tiers", {}).keys())
    start_tier = state.get("config", {}).get("start_tier") or TranslationTier.HAIKU.value
    # node.tier is only set by `vuemorphic escalate` — Phase A assignments are nulled out.
    # NULL means run on start_tier; non-null means operator manually escalated this node.
    tier = node.tier.value if node.tier else start_tier
    # Hard cap: if the tier isn't in model_tiers (e.g. opus removed from config), fall back.
    if available_tiers and tier not in available_tiers:
        _TIER_RANK = {TranslationTier.HAIKU.value: 0, TranslationTier.SONNET.value: 1, TranslationTier.OPUS.value: 2}
        tier = max(available_tiers, key=lambda t: _TIER_RANK.get(t, 0))
    logger.info(
        "Worker %d: processing %s (tier=%s, bfs_level=%s)",
        state.get("worker_id", 0), node.node_id, tier, node.bfs_level,
    )

    return {
        "current_node_id": node.node_id,
        "current_tier": tier,
        "current_prompt": None,
        "current_vue_content": None,
        "current_raw_response": None,
        "attempt_count": 0,
        "last_error": None,
        "verify_status": None,
        "failure_analysis": None,
        "cascade_count": 0,
        "done": False,
    }


def build_context(state: VuemorphicState) -> dict:
    """Assemble the Claude conversion prompt for the current node."""
    manifest = Manifest.load(_db(state))
    node_id = state["current_node_id"]
    node = manifest.get_node(node_id) or manifest.nodes[node_id]
    workspace = _db(state).parent

    prompt = build_prompt(
        node=node,
        manifest=manifest,
        config=state["config"],
        target_vue_path=Path(state["target_vue_path"]),
        snippets_dir=Path(state["snippets_dir"]),
        workspace=workspace,
        last_error=state.get("last_error"),
        attempt_count=state.get("attempt_count", 0),
        supervisor_hint=state.get("supervisor_hint"),
        previous_failure_analysis=state.get("failure_analysis"),
    )
    return {"current_prompt": prompt, "supervisor_hint": None}


_EMPTY_BODY_RE = re.compile(r"^\s*\w[\w\s<>,*()?:]*\(\s*\)\s*\{[\s]*\}\s*$", re.DOTALL)


def invoke_agent(state: VuemorphicState) -> dict:
    """Call the Claude Code subprocess and capture the full .vue file content.

    -- MECHANICAL PASS SLOT --
    A future v1 mechanical pass (ast-grep substitutions) will run here, between
    pick_next_node and build_context, to handle purely context-free idioms before
    the LLM sees the file. For v0, this is a pass-through.
    """
    node_id = state.get("current_node_id", "")
    manifest = Manifest.load(_db(state))
    node = manifest.get_node(node_id)

    tier = state.get("current_tier") or TranslationTier.HAIKU.value
    model = state.get("config", {}).get("model_tiers", {}).get(tier)

    workspace = _db(state).parent
    cwd = str(workspace)

    attempt = state.get("attempt_count", 0)
    prompt_log_dir = workspace / "_prompt_logs"
    safe_node = node_id.replace("/", "_").replace(":", "_")
    label = f"{safe_node}__{tier}_attempt{attempt}"

    try:
        backend = state.get("config", {}).get("backend", "claude")
        if backend == "ollama":
            ollama_model = state.get("config", {}).get("ollama_model", "qwen2.5-coder:32b")
            ollama_url = state.get("config", {}).get("ollama_base_url", "http://localhost:11434")
            response = invoke_ollama(
                prompt=state["current_prompt"],
                cwd=cwd,
                tier=tier,
                model=ollama_model,
                base_url=ollama_url,
                prompt_log_dir=prompt_log_dir,
                label=label,
            )
        elif backend == "anthropic-api":
            response = invoke_anthropic_api(
                prompt=state["current_prompt"],
                cwd=cwd,
                tier=tier,
                model=model,
                prompt_log_dir=prompt_log_dir,
                label=label,
            )
        elif backend == "local":
            local_model = state.get("config", {}).get("local_model", "qwen2.5-coder:32b")
            response = invoke_pi(
                prompt=state["current_prompt"],
                cwd=cwd,
                tier=tier,
                model=local_model,
                prompt_log_dir=prompt_log_dir,
                label=label,
            )
        else:
            response = invoke_claude(
                prompt=state["current_prompt"],
                cwd=cwd,
                tier=tier,
                model=model,
                prompt_log_dir=prompt_log_dir,
                label=label,
            )
        # Strip ---BLOCKED--- first (it comes after ---SUMMARY--- in the output).
        # Keep the raw response (with summary intact) for update_manifest.
        raw_with_summary = response
        failure_analysis: str | None = None
        if _BLOCKED_DELIMITER in response:
            pre_blocked, blocked_part = response.split(_BLOCKED_DELIMITER, 1)
            failure_analysis = blocked_part.strip() or None
            raw_with_summary = pre_blocked  # summary is in here; BLOCKED is dropped
            response = pre_blocked

        # Strip ---SUMMARY--- so verify only sees clean .vue content.
        if _SUMMARY_DELIMITER in response:
            response = response.split(_SUMMARY_DELIMITER, 1)[0]

        return {
            "current_vue_content": response,
            "current_raw_response": raw_with_summary,
            "last_error": None,
            "failure_analysis": failure_analysis,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("invoke_claude failed for %s: %s", node_id, exc)
        err_log = prompt_log_dir / f"{safe_node}__{tier}_attempt{attempt}_error.txt"
        try:
            prompt_log_dir.mkdir(parents=True, exist_ok=True)
            err_log.write_text(str(exc))
        except Exception:  # noqa: BLE001
            pass
        return {"current_vue_content": None, "last_error": str(exc), "failure_analysis": None}


def verify(state: VuemorphicState) -> dict:
    """Run the tiered Vue oracle verification."""
    manifest = Manifest.load(_db(state))
    node = manifest.get_node(state["current_node_id"]) or manifest.nodes[state["current_node_id"]]
    vue_content = state.get("current_vue_content")

    if vue_content is None:
        return {
            "verify_status": VerifyStatus.COMPILE.value,
            "last_error": state.get("last_error") or "Agent invocation failed (no content returned)",
        }

    result = verify_vue_file(
        node_id=node.node_id,
        vue_content=vue_content,
        target_dir=Path(state["target_vue_path"]),
        component_name=node.node_id,
    )
    return {
        "verify_status": result.status.value,
        "last_error": result.error or None,
    }


def _escalate_tier(tier: str, config: dict | None = None) -> str | None:
    allow_opus = (config or {}).get("allow_opus", False)
    if tier == TranslationTier.HAIKU.value:
        return TranslationTier.SONNET.value
    if tier == TranslationTier.SONNET.value:
        return TranslationTier.OPUS.value if allow_opus else None
    return None


def route_after_verify(state: VuemorphicState) -> str:
    if state["verify_status"] == VerifyStatus.PASS:
        return "update_manifest"

    tier = state.get("current_tier") or TranslationTier.HAIKU.value
    attempt = state.get("attempt_count", 0) + 1
    cfg_max = state.get("config", {}).get("max_attempts")
    if isinstance(cfg_max, int):
        max_attempts = cfg_max
    elif isinstance(cfg_max, dict):
        max_attempts = cfg_max.get(tier, _MAX_ATTEMPTS.get(tier, _DEFAULT_MAX_ATTEMPTS))
    else:
        max_attempts = _MAX_ATTEMPTS.get(tier, _DEFAULT_MAX_ATTEMPTS)

    # CASCADE: errors are in OTHER files, not this node's output.
    # Requeue — do not penalize this node for another file's breakage.
    if state["verify_status"] == VerifyStatus.CASCADE:
        logger.warning(
            "CASCADE: %s requeued — vue-tsc errors are in other files, not this component",
            state.get("current_node_id"),
        )
        return "requeue"

    if attempt >= max_attempts:
        cfg = state.get("config", {})
        no_escalate = cfg.get("no_escalate", False)
        if no_escalate:
            return "queue_for_review"
        if _escalate_tier(tier, cfg) is None:
            return "supervisor"
        return "escalate"
    return "retry"


def retry_node(state: VuemorphicState) -> dict:
    return {"attempt_count": state.get("attempt_count", 0) + 1}


_MAX_CASCADE_REQUEUES = 3


def requeue_node(state: VuemorphicState) -> dict:
    """Reset a cascade-failed node to NOT_STARTED so it retries when the project is clean.

    After _MAX_CASCADE_REQUEUES attempts, gives up and sends to human_review to
    prevent an infinite loop when the blocking file never gets fixed.
    """
    node_id = state["current_node_id"]
    cascade_count = state.get("cascade_count", 0) + 1
    manifest = Manifest.load(_db(state))

    if cascade_count > _MAX_CASCADE_REQUEUES:
        logger.warning(
            "CASCADE loop: %s requeued %d times — sending to human_review",
            node_id, cascade_count,
        )
        manifest.update_node(
            _db(state), node_id,
            status=NodeStatus.HUMAN_REVIEW,
            last_error=f"cascade loop after {cascade_count} requeues",
        )
        return {
            "current_node_id": None,
            "attempt_count": 0,
            "cascade_count": 0,
            "last_error": None,
            "verify_status": None,
            "failure_analysis": None,
        }

    manifest.update_node(
        _db(state), node_id,
        status=NodeStatus.NOT_STARTED,
        attempt_count=0,
        last_error=None,
    )
    return {
        "current_node_id": None,
        "attempt_count": 0,
        "cascade_count": cascade_count,
        "last_error": None,
        "verify_status": None,
        "failure_analysis": None,
    }


def escalate_node(state: VuemorphicState) -> dict:
    tier = state.get("current_tier") or TranslationTier.HAIKU.value
    cfg = state.get("config", {})
    next_tier = _escalate_tier(tier, cfg) or TranslationTier.SONNET.value
    logger.info("Escalating %s: %s → %s", state.get("current_node_id"), tier, next_tier)
    return {"current_tier": next_tier, "attempt_count": 0}


def transform_data_module(state: VuemorphicState) -> dict:
    """Code-based transform for DATA_MODULE nodes — no agent call needed.

    Strips window assignments, adds TypeScript export, infers value interface
    from the first entry, and commits directly to the Vue project git repo.
    """
    import re as _re
    import json as _json

    node_id = state["current_node_id"]
    manifest = Manifest.load(_db(state))
    node = manifest.get_node(node_id)
    source = node.source_text if node else ""

    # Extract ONLY the target const declaration — strip everything else
    # (helper functions, window assignments, comments, other consts)
    const_match = _re.search(
        rf"(const\s+{_re.escape(node_id)}\s*=\s*\{{)",
        source,
    )
    if const_match:
        # Find the matching closing brace by counting depth
        start = const_match.start()
        depth = 0
        end = start
        for i, ch in enumerate(source[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        # Consume optional trailing semicolon
        if end < len(source) and source[end] == ";":
            end += 1
        const_body = source[start:end]
        # Replace const with export const + Record<string, any> type
        const_body = _re.sub(
            rf"const\s+{_re.escape(node_id)}\s*=",
            f"export const {node_id}: Record<string, any> =",
            const_body,
            count=1,
        )
        source = const_body
    else:
        # Fallback: strip window assignments and add export
        source = _re.sub(r"Object\.assign\(window\s*,[^)]+\)\s*;?\n?", "", source)
        source = _re.sub(
            rf"(const\s+{_re.escape(node_id)}\s*=\s*\{{)",
            rf"export const {node_id}: Record<string, any> = {{",
            source,
            count=1,
        )

    # Write to Vue project src/registries/ (create dir if needed)
    target_dir = Path(state["target_vue_path"])
    out_dir = target_dir / "src" / "registries"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{node_id}.ts"
    out_path.write_text(source.strip() + "\n", encoding="utf-8")

    # Save snippet
    safe_id = node_id.replace("/", "_")
    snippet_dir = Path(state["snippets_dir"])
    snippet_dir.mkdir(parents=True, exist_ok=True)
    snippet_path = snippet_dir / f"{safe_id}.ts"
    snippet_path.write_text(source.strip() + "\n", encoding="utf-8")

    manifest.update_node(
        _db(state),
        node_id,
        status=NodeStatus.CONVERTED,
        snippet_path=str(snippet_path),
        attempt_count=0,
        summary_text=f"Data registry: {node_id} ({len(source)} chars)",
    )
    logger.info("DATA_MODULE: %s → %s", node_id, out_path)

    # Commit to the Vue project git repo
    _git_commit_conversion(out_path, node_id, f"Data registry ported from React source.", "data")

    return {
        "nodes_this_run": state.get("nodes_this_run", 0) + 1,
        "current_node_id": node_id,
        "current_tier": "data",
        "attempt_count": 0,
    }


def setup_worker_clones(target_path: Path, parallelism: int) -> list[Path]:
    """Create N git worktrees of the Vue target project for isolated parallel verification.

    Each worktree gets its own branch (worker-0, worker-1, ...) and a symlink to
    node_modules/ from the main clone so vue-tsc doesn't need a separate install.

    Returns the list of worktree paths (one per worker, all absolute).
    """
    import subprocess as _sp
    import shutil as _sh

    target_path = target_path.resolve()
    main_modules = target_path / "node_modules"
    worktrees: list[Path] = []

    for i in range(parallelism):
        wt_path = target_path.parent / f"{target_path.name}-worker-{i}"
        branch = f"worker-{i}"

        # Remove stale worktree if it exists
        if wt_path.exists():
            _sp.run(["git", "worktree", "remove", "--force", str(wt_path)],
                    cwd=target_path, capture_output=True)
            if wt_path.exists():
                _sh.rmtree(wt_path)

        # Delete stale branch if it exists
        _sp.run(["git", "branch", "-D", branch], cwd=target_path, capture_output=True)

        _sp.run(
            ["git", "worktree", "add", "-b", branch, str(wt_path), "HEAD"],
            cwd=target_path, check=True, capture_output=True,
        )

        # Symlink node_modules so vue-tsc resolves without a fresh install
        wt_modules = wt_path / "node_modules"
        if not wt_modules.exists() and main_modules.exists():
            wt_modules.symlink_to(main_modules)

        worktrees.append(wt_path)
        logger.info("Worker %d: worktree at %s (branch %s)", i, wt_path, branch)

    return worktrees


def teardown_worker_clones(target_path: Path, parallelism: int) -> None:
    """Merge each worker branch back to main and remove its worktree."""
    import subprocess as _sp

    target_path = target_path.resolve()

    for i in range(parallelism):
        wt_path = target_path.parent / f"{target_path.name}-worker-{i}"
        branch = f"worker-{i}"
        try:
            # Cherry-pick all commits from the worker branch onto main
            # Each commit is a single .vue file — no conflicts possible
            out = _sp.run(
                ["git", "log", "--format=%H", f"HEAD..{branch}"],
                cwd=target_path, capture_output=True, text=True, check=True,
            ).stdout.strip()
            worker_commits = [c for c in out.split("\n") if c]
            for sha in reversed(worker_commits):  # oldest first
                _sp.run(["git", "cherry-pick", sha],
                        cwd=target_path, check=True, capture_output=True)
            # Remove the worktree and branch
            _sp.run(["git", "worktree", "remove", "--force", str(wt_path)],
                    cwd=target_path, capture_output=True)
            _sp.run(["git", "branch", "-D", branch],
                    cwd=target_path, capture_output=True)
            logger.info("Merged worker-%d (%d commits) → main", i, len(worker_commits))
        except _sp.CalledProcessError as exc:
            logger.error("Failed to merge worker-%d: %s", i, exc.stderr.decode()[:300])


_SUMMARY_DELIMITER = "---SUMMARY---"


def _git_commit_conversion(
    vue_path: Path,
    node_id: str,
    summary: str | None,
    tier: str | None,
) -> None:
    """Stage and commit the converted .vue file inside the Vue target project.

    Silently skips if the directory is not a git repo (e.g. during tests).
    The agent's ---SUMMARY--- text becomes the commit message body.
    """
    import subprocess as _sp
    target_dir = vue_path.parent.parent.parent  # corpora/claude-design-vue/
    if not (target_dir / ".git").exists():
        return
    rel_path = vue_path.relative_to(target_dir)
    body = summary.strip() if summary else ""
    commit_msg = f"feat(convert): {node_id} [{tier or 'haiku'}]\n\n{body}"
    try:
        _sp.run(["git", "add", str(rel_path)], cwd=target_dir, check=True, capture_output=True)
        _sp.run(["git", "commit", "-m", commit_msg], cwd=target_dir, check=True, capture_output=True)
    except _sp.CalledProcessError as exc:
        logger.warning("git commit failed for %s: %s", node_id, exc.stderr.decode()[:200])


def update_manifest(state: VuemorphicState) -> dict:
    """Save the .vue file to disk and mark the node CONVERTED in the DB.

    If the agent response contains ---SUMMARY---, the text before it is the
    .vue file content and the text after is a 1-2 sentence summary stored
    in summary_text for use as dense context in caller prompts.
    """
    node_id = state["current_node_id"]
    vue_content = (state.get("current_vue_content") or "").strip()

    # Extract summary from the raw response (which still has ---SUMMARY--- intact)
    raw_response = state.get("current_raw_response") or vue_content
    if _SUMMARY_DELIMITER in raw_response:
        summary = raw_response.split(_SUMMARY_DELIMITER, 1)[1].strip()
    else:
        summary = None

    manifest = Manifest.load(_db(state))
    node = manifest.get_node(node_id) or manifest.nodes[node_id]

    # Write the final .vue file to the target project
    components_dir = Path(state["target_vue_path"]) / "src" / "components"
    components_dir.mkdir(parents=True, exist_ok=True)
    vue_path = components_dir / f"{node_id}.vue"
    vue_path.write_text(vue_content)

    # Also save a snippet copy for dep context loading
    safe_id = node_id.replace("/", "_").replace(":", "_")
    snippet_dir = Path(state["snippets_dir"])
    snippet_dir.mkdir(parents=True, exist_ok=True)
    snippet_path = snippet_dir / f"{safe_id}.vue"
    snippet_path.write_text(vue_content)

    attempt_count = state.get("attempt_count", 0)
    manifest.update_node(
        _db(state),
        node_id,
        status=NodeStatus.CONVERTED,
        snippet_path=str(snippet_path),
        attempt_count=attempt_count,
        summary_text=summary,
    )
    logger.info("CONVERTED: %s → %s", node_id, vue_path)
    _git_commit_conversion(vue_path, node_id, summary, state.get("current_tier"))
    return {
        "nodes_this_run": state.get("nodes_this_run", 0) + 1,
        "current_node_id": node_id,
        "current_tier": state.get("current_tier") or TranslationTier.HAIKU.value,
        "attempt_count": attempt_count,
    }


def queue_for_review(state: VuemorphicState) -> dict:
    """Add the node to the human review queue and mark it HUMAN_REVIEW in the DB."""
    node_id = state["current_node_id"]
    manifest = Manifest.load(_db(state))
    node = manifest.get_node(node_id) or manifest.nodes[node_id]

    raw_analysis = state.get("failure_analysis") or ""
    category_match = _CATEGORY_RE.search(raw_analysis)
    failure_category = category_match.group(1) if category_match else None

    manifest.update_node(
        _db(state),
        node_id,
        status=NodeStatus.HUMAN_REVIEW,
        attempt_count=state.get("attempt_count", 0),
        last_error=state.get("last_error"),
        failure_category=failure_category,
        failure_analysis=raw_analysis or None,
    )

    entry = {
        "node_id": node_id,
        "tier": state.get("current_tier"),
        "attempts": state.get("attempt_count", 0),
        "last_error": state.get("last_error", ""),
        "source_text_preview": node.source_text[:300],
    }
    logger.warning("HUMAN_REVIEW: %s (exhausted all retries)", node_id)
    return {"review_queue": [entry], "nodes_this_run": state.get("nodes_this_run", 0) + 1}


def route_after_supervisor(state: VuemorphicState) -> str:
    if state.get("supervisor_hint") is not None:
        return "build_context"
    return "queue_for_review"


def supervisor_node(state: VuemorphicState) -> dict:
    """Generate a targeted hint via Sonnet, then optionally interrupt for human review."""
    node_id = state["current_node_id"]
    manifest = Manifest.load(_db(state))
    node = manifest.get_node(node_id) or manifest.nodes[node_id]

    hint_prompt = (
        f"You are reviewing a failed React→Vue translation.\n\n"
        f"Node: {node_id}\n"
        f"Last error:\n{(state.get('last_error') or 'unknown')[:500]}\n\n"
        f"React source:\n```jsx\n{node.source_text[:600]}\n```\n\n"
        f"Generate a 2-3 sentence concrete hint for the translator's next attempt. "
        f"Focus on the specific error and what to do differently. Be concrete, not generic."
    )

    try:
        hint = invoke_claude(
            prompt=hint_prompt,
            cwd=state["target_vue_path"],
            tier="sonnet",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("supervisor_node hint generation failed for %s: %s", node_id, exc)
        hint = None

    review_mode = state.get("review_mode", "auto")
    if review_mode == "interactive":
        from langgraph.types import interrupt as lg_interrupt
        payload = {
            "node_id": node_id,
            "error": state.get("last_error", ""),
            "supervisor_hint": hint,
            "source_preview": node.source_text[:500],
        }
        human_response = lg_interrupt(payload)
        if isinstance(human_response, dict):
            if human_response.get("skip"):
                return {"supervisor_hint": None, "interrupt_payload": None}
            if human_response.get("hint"):
                hint = str(human_response["hint"])

    if hint is not None:
        return {"supervisor_hint": hint, "interrupt_payload": None, "attempt_count": 0}
    return {"supervisor_hint": None, "interrupt_payload": None}
