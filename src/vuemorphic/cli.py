"""Vuemorphic CLI entry point."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import typer

app = typer.Typer(name="vuemorphic", help="Agentic React→Vue 3 translation harness.",
                   no_args_is_help=True)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "phase_a_scripts"

_DEFAULT_DB = "vuemorphic.db"


@app.command("phase-a")
def phase_a(
    config: Path = typer.Option("vuemorphic.config.json", "--config", "-c"),
    manifest_out: Path = typer.Option("conversion_manifest.json", "--manifest-out"),
    db: Path = typer.Option(_DEFAULT_DB, "--db", help="Output SQLite DB path for Phase B."),
    skip_tiers: bool = typer.Option(False, "--skip-tiers",
                                     help="Skip tier classification entirely."),
    heuristic_tiers: bool = typer.Option(False, "--heuristic-tiers",
                                          help="Use deterministic heuristic tiers (no API call)."),
) -> None:
    """Run the full Phase A analysis pipeline.

    Steps: A1 extract AST → A2 detect idioms → A3 build Vue skeletons →
           A4 import JSON→SQLite → A5 topology → A6 classify tiers.
    """
    cfg = json.loads(config.read_text())
    tsconfig    = cfg["tsconfig"]
    source_root = cfg["source_repo"]
    target_repo = Path(cfg["target_repo"])
    model       = cfg["model_tiers"]["haiku"]
    db          = db.resolve()

    # A1: AST extraction (React JSX, writes JSON manifest)
    typer.echo("A1: extracting AST...")
    subprocess.run(
        ["npx", "tsx", str(_SCRIPTS_DIR / "extract_ast.ts"),
         "--tsconfig", tsconfig,
         "--source-root", source_root,
         "--out", str(manifest_out)],
        check=True,
    )

    # A2: Idiom detection (updates JSON manifest in place)
    typer.echo("A2: detecting idioms...")
    subprocess.run(
        ["npx", "tsx", str(_SCRIPTS_DIR / "detect_idioms.ts"),
         "--manifest", str(manifest_out)],
        check=True,
    )

    # A3: Build Vue project scaffold + skeletons from Python contract extractor
    # Uses the Python extractor because it produces full
    # ComponentContract objects with vue_imports, prop_defaults, etc. that
    # the skeleton builder needs.
    typer.echo("A3: scaffolding Vue project and building skeletons...")
    from vuemorphic.analysis.component_contracts import extract_contracts, setup_vue_project
    from vuemorphic.skeleton.build import build_all_skeletons
    setup_vue_project(str(target_repo), cfg)
    contracts = extract_contracts(source_root, cfg)
    skeletons = build_all_skeletons(contracts, str(target_repo))
    typer.echo(f"  Built {len(skeletons)} skeleton(s) in {target_repo}")

    # A4: Import JSON manifest → SQLite (needed for topology + tiers + Phase B)
    typer.echo("A4: importing manifest to SQLite...")
    _import_json_to_db(manifest_out, db)
    typer.echo(f"  DB: {db}")

    # A5: Topological sort (on SQLite)
    typer.echo("A5: computing topological order...")
    from vuemorphic.models.manifest import Manifest
    manifest = Manifest.load(db)
    try:
        manifest.compute_topology()
    except ValueError as exc:
        typer.echo(f"Warning: {exc} — continuing without full topology", err=True)

    # A6: Tier classification
    if skip_tiers:
        typer.echo("A6: skipped (--skip-tiers)")
    elif heuristic_tiers:
        typer.echo("A6: classifying tiers (heuristic, no API call)...")
        from vuemorphic.analysis.classify_tiers import classify_manifest_heuristic
        classify_manifest_heuristic(db)
    else:
        typer.echo("A6: classifying tiers...")
        from vuemorphic.analysis.classify_tiers import classify_manifest
        classify_manifest(db, model=model)

    typer.echo("Phase A complete.")
    typer.echo(f"\nNext step: vuemorphic phase-b --db {db} --max-nodes 5")


def _import_json_to_db(manifest_json: Path, db: Path) -> None:
    """Import a JSON conversion manifest into SQLite. Upserts all nodes."""
    import json as _json
    import sqlite3 as _sqlite3

    from vuemorphic.models.db import NodeRecord, ManifestMeta
    from vuemorphic.models.manifest import ConversionNode, _get_engine
    from sqlmodel import Session, SQLModel

    data = _json.loads(manifest_json.read_text())
    nodes_raw = data.get("nodes", {})

    engine = _get_engine(db)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        meta = session.get(ManifestMeta, 1)
        if meta is None:
            meta = ManifestMeta(
                id=1,
                version=data.get("version", "1.0"),
                source_repo=data.get("source_repo", ""),
                generated_at=data.get("generated_at", ""),
            )
        else:
            meta.version = data.get("version", meta.version)
            meta.source_repo = data.get("source_repo", meta.source_repo)
            meta.generated_at = data.get("generated_at", meta.generated_at)
        session.add(meta)

        inserted = updated = 0
        for node_id, raw in nodes_raw.items():
            raw["node_id"] = raw.get("node_id") or node_id
            try:
                node = ConversionNode.model_validate(raw)
            except Exception:
                continue
            row = session.get(NodeRecord, node_id)
            if row is None:
                session.add(NodeRecord.from_conversion_node(node))
                inserted += 1
            else:
                new_row = NodeRecord.from_conversion_node(node)
                new_row.status = row.status
                new_row.snippet_path = row.snippet_path
                new_row.attempt_count = row.attempt_count
                new_row.last_error = row.last_error
                session.add(new_row)
                updated += 1
        session.commit()

    with _sqlite3.connect(str(db)) as con:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    logging.getLogger(__name__).info(
        "Imported manifest: %d inserted, %d updated (%d total nodes)",
        inserted, updated, len(nodes_raw),
    )


@app.command("classify-tiers")
def classify_tiers(
    manifest: Path = typer.Option("conversion_manifest.json", "--manifest"),
    heuristic: bool = typer.Option(False, "--heuristic",
                                    help="Use deterministic rules (no API call)."),
    config: Path = typer.Option("vuemorphic.config.json", "--config", "-c"),
) -> None:
    """Run tier classification (A4) on an existing manifest without re-running Phase A.

    By default uses Claude Haiku (requires ANTHROPIC_API_KEY). Pass --heuristic to
    use deterministic rules instead.
    """
    from vuemorphic.analysis.classify_tiers import classify_manifest, classify_manifest_heuristic

    if heuristic:
        typer.echo("Classifying tiers (heuristic, no API call)...")
        classify_manifest_heuristic(manifest)
    else:
        cfg = json.loads(config.read_text())
        model = cfg["model_tiers"]["haiku"]
        typer.echo(f"Classifying tiers with {model}...")
        classify_manifest(manifest, model=model)

    import json as _json
    with open(manifest) as f:
        m = _json.load(f)
    nodes = m["nodes"]
    from collections import Counter
    tier_counts = Counter(n.get("tier") for n in nodes.values())
    total = len(nodes)
    typer.echo(f"Done. {total} nodes:")
    for tier, count in sorted(tier_counts.items(), key=lambda x: x[0] or ""):
        typer.echo(f"  {tier or 'None':8s}  {count}")


@app.command("import-manifest")
def import_manifest(
    manifest: Path = typer.Argument(..., help="Path to conversion_manifest.json"),
    db: Path = typer.Option("vuemorphic.db", "--db", help="Output SQLite DB path."),
) -> None:
    """Import a JSON conversion manifest into SQLite.

    One-time migration: reads conversion_manifest.json, creates vuemorphic.db,
    and bulk-inserts all nodes. Phase A keeps writing JSON; run this once to
    seed the DB before starting Phase B.
    """
    import json as _json
    from vuemorphic.models.db import NodeRecord, ManifestMeta
    from vuemorphic.models.manifest import ConversionNode, _get_engine
    from sqlmodel import Session, SQLModel

    if not manifest.exists():
        typer.echo(f"Error: {manifest} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Reading {manifest}...")
    data = _json.loads(manifest.read_text())
    nodes_raw = data.get("nodes", {})

    engine = _get_engine(db)
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        # Upsert manifest meta
        meta = session.get(ManifestMeta, 1)
        if meta is None:
            meta = ManifestMeta(
                id=1,
                version=data.get("version", "1.0"),
                source_repo=data.get("source_repo", ""),
                generated_at=data.get("generated_at", ""),
            )
        else:
            meta.version = data.get("version", meta.version)
            meta.source_repo = data.get("source_repo", meta.source_repo)
            meta.generated_at = data.get("generated_at", meta.generated_at)
        session.add(meta)

        # Upsert all nodes
        inserted = updated = 0
        for node_id, raw in nodes_raw.items():
            raw["node_id"] = raw.get("node_id") or node_id
            node = ConversionNode.model_validate(raw)
            row = session.get(NodeRecord, node_id)
            if row is None:
                session.add(NodeRecord.from_conversion_node(node))
                inserted += 1
            else:
                # Preserve Phase B progress (status, snippet_path, attempt_count)
                # but refresh everything else from JSON
                new_row = NodeRecord.from_conversion_node(node)
                new_row.status = row.status
                new_row.snippet_path = row.snippet_path
                new_row.attempt_count = row.attempt_count
                new_row.last_error = row.last_error
                session.add(new_row)
                updated += 1
        session.commit()

    # Force WAL checkpoint so all rows are in the main DB file immediately.
    # Without this, data lives only in the WAL — a killed process zeroes the WAL
    # and everything is lost on next open.
    import sqlite3 as _sqlite3
    with _sqlite3.connect(str(db.resolve())) as _con:
        _con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    typer.echo(f"Done. {db}: {inserted} inserted, {updated} updated ({len(nodes_raw)} total).")


@app.command("phase-b")
def phase_b(
    config: Path = typer.Option("vuemorphic.config.json", "--config", "-c"),
    db: Path = typer.Option("vuemorphic.db", "--db", help="Path to vuemorphic SQLite manifest DB."),
    snippets_dir: Path = typer.Option("snippets", "--snippets-dir"),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the first node's prompt then exit — no API calls made.",
    ),
    max_nodes: int = typer.Option(
        None, "--max-nodes",
        help="Stop after translating this many nodes. Useful for smoke tests.",
    ),
) -> None:
    """Run Phase B: translate all nodes in topological order via Claude Code.

    Requires a compiled skeleton from ``vuemorphic phase-a`` and an vuemorphic.db
    seeded by ``vuemorphic import-manifest``.
    Structural nodes (class/interface/enum/type_alias) are auto-converted first.
    Exhausted nodes are written to ``review_queue.json``.
    """
    import json as _json

    from vuemorphic.graph.nodes import build_context, pick_next_node
    from vuemorphic.graph.state import VuemorphicState
    from vuemorphic.models.manifest import Manifest as _Manifest

    # Always use absolute path — subprocess cwd changes must never create a rogue DB
    db = db.resolve()

    if not db.exists():
        typer.echo(
            f"Error: {db} not found. Run `vuemorphic import-manifest conversion_manifest.json` first.",
            err=True,
        )
        raise typer.Exit(1)

    # Safety: backup DB before starting a run so we can recover from wipes
    import shutil as _shutil
    backup = db.with_suffix(".db.bak")
    _shutil.copy2(str(db), str(backup))
    typer.echo(f"DB backed up to {backup}")

    cfg = _json.loads(config.read_text())
    manifest_obj = _Manifest.load(db)

    count = manifest_obj.auto_convert_structural_nodes(db)
    if count:
        typer.echo(f"Auto-converted {count} structural nodes.")

    snippets_dir.mkdir(parents=True, exist_ok=True)
    target_path = Path(cfg.get("target_repo", "corpora/claude-design-vue"))

    initial_state = VuemorphicState(
        db_path=str(db.resolve()),
        target_vue_path=str(target_path.resolve()),
        snippets_dir=str(snippets_dir.resolve()),
        config=cfg,
        worker_id=0,
        current_node_id=None,
        current_prompt=None,
        current_vue_content=None,
        current_tier=None,
        attempt_count=0,
        last_error=None,
        verify_status=None,
        review_queue=[],
        done=False,
        max_nodes=max_nodes,
        nodes_this_run=0,
        supervisor_hint=None,
        interrupt_payload=None,
        review_mode=cfg.get("review_mode", "auto"),
        failure_analysis=None,
    )

    if dry_run:
        s = pick_next_node(initial_state)
        if s.get("done"):
            typer.echo("No eligible nodes — all CONVERTED or blocked.")
            return
        # Merge update back into state for build_context
        merged = {**initial_state, **s}
        s2 = build_context(merged)
        node_id = s.get("current_node_id")
        prompt = s2.get("current_prompt", "")
        typer.echo(f"Node: {node_id}")
        typer.echo(f"Prompt length: {len(prompt)} chars")
        typer.echo("\n--- prompt (first 3000 chars) ---")
        typer.echo(prompt[:3000])
        return

    parallelism = cfg.get("parallelism", 1)

    if parallelism > 1:
        import asyncio
        from vuemorphic.graph.graph import build_graph
        from vuemorphic.graph.nodes import setup_worker_clones, teardown_worker_clones

        typer.echo(f"Parallel mode: {parallelism} workers")
        worktrees = setup_worker_clones(target_path, parallelism)

        async def run_parallel() -> list[dict]:
            graphs = [build_graph() for _ in range(parallelism)]
            coros = [
                g.ainvoke({
                    **initial_state,
                    "worker_id": i,
                    "target_vue_path": str(wt),
                })
                for i, (g, wt) in enumerate(zip(graphs, worktrees))
            ]
            return list(await asyncio.gather(*coros))

        results = asyncio.run(run_parallel())
        teardown_worker_clones(target_path, parallelism)

        # Merge review queues from all workers
        review_queue: list[dict] = []
        for r in results:
            review_queue.extend(r.get("review_queue", []))
    else:
        from vuemorphic.graph.graph import translation_graph
        final_state = translation_graph.invoke(initial_state)
        review_queue = final_state.get("review_queue", [])

    if review_queue:
        import json
        rq_path = Path("review_queue.json")
        rq_path.write_text(json.dumps(review_queue, indent=2))
        typer.echo(f"\n{len(review_queue)} nodes queued for human review → {rq_path}")

    manifest_final = _Manifest.load(db)
    typer.echo("\nPhase B complete.")


@app.command("blocked")
def blocked(
    db: Path = typer.Option("vuemorphic.db", "--db", help="Path to vuemorphic SQLite manifest DB."),
) -> None:
    """Show nodes stuck in human_review and the not-started nodes waiting on them."""
    from vuemorphic.models.manifest import Manifest

    db = db.resolve()
    if not db.exists():
        typer.echo(f"Error: {db} not found.", err=True)
        raise typer.Exit(1)

    manifest = Manifest.load(db)
    report = manifest.blocked_report()

    if not report:
        typer.echo("No blocked nodes.")
        return

    by_category: dict[str, list[str]] = {}
    for blocker_id, info in report.items():
        cat = info["failure_category"] or "unknown"
        by_category.setdefault(cat, []).append(blocker_id)

    typer.echo(f"\n{'='*60}")
    typer.echo(f"BLOCKED NODES REPORT — {len(report)} node(s) in human_review")
    typer.echo(f"{'='*60}\n")

    typer.echo("By category:")
    for cat, ids in sorted(by_category.items()):
        typer.echo(f"  {cat:20s}  {len(ids)} node(s): {', '.join(ids)}")

    typer.echo()
    for blocker_id, info in report.items():
        waiting = info["waiting"]
        category = info["failure_category"] or "unknown"
        analysis = info["failure_analysis"] or "(no diagnosis recorded)"

        typer.echo(f"--- {blocker_id} [{category}] ---")
        if waiting:
            typer.echo(f"  Blocking: {', '.join(waiting)}")
        else:
            typer.echo("  Blocking: (no dependents — safe to discard)")

        for line in analysis.splitlines():
            if line.startswith("FIX:"):
                typer.echo(f"  {line}")
                break
        typer.echo()


@app.command("escalate")
def escalate(
    node_id: str = typer.Argument(..., help="node_id to escalate (must be in human_review)"),
    db: Path = typer.Option("vuemorphic.db", "--db", help="Path to vuemorphic SQLite manifest DB."),
    tier: str = typer.Option("sonnet", "--tier", help="Tier to escalate to: sonnet | opus"),
) -> None:
    """Manually escalate a single human_review node to a higher model tier.

    Resets the node to not_started and clears attempt_count so Phase B picks it up next run.
    Run 'vuemorphic blocked' first to see candidates and their failure analyses.
    """
    from vuemorphic.models.manifest import Manifest, NodeStatus, TranslationTier

    valid_tiers = {t.value for t in TranslationTier}
    if tier not in valid_tiers:
        typer.echo(f"Error: --tier must be one of {sorted(valid_tiers)}", err=True)
        raise typer.Exit(1)

    db = db.resolve()
    if not db.exists():
        typer.echo(f"Error: {db} not found.", err=True)
        raise typer.Exit(1)

    manifest = Manifest.load(db)
    node = manifest.get_node(node_id)
    if node is None:
        typer.echo(f"Error: node '{node_id}' not found in DB.", err=True)
        raise typer.Exit(1)

    manifest.update_node(
        db,
        node_id,
        status=NodeStatus.NOT_STARTED,
        tier=TranslationTier(tier),
        attempt_count=0,
        last_error=None,
    )
    typer.echo(f"Escalated {node_id}: human_review → not_started (tier={tier})")
    typer.echo(f"Run 'vuemorphic phase-b --db {db} --max-nodes 1' to process it.")


@app.command("serve")
def serve(
    config: Path = typer.Option("vuemorphic.config.json", "--config", "-c"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    db_path: str = typer.Option("vuemorphic_checkpoints.db", "--db",
                                 help="Path to SqliteSaver checkpoint DB"),
    gui_dist: str = typer.Option(None, "--gui-dist",
                                  help="Path to built Vue GUI dist/ directory"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)"),
) -> None:
    """Start the FastAPI server for Phase B monitoring and control.

    Opens the vuemorphic dashboard at http://<host>:<port>/
    Start a run with: POST /run  {manifest_path, target_path, ...}
    Stream progress with: GET /stream/{thread_id}
    """
    import uvicorn
    from vuemorphic.serve.app import create_app

    typer.echo(f"Starting vuemorphic serve on http://{host}:{port}")
    if gui_dist:
        typer.echo(f"Serving GUI from {gui_dist}")
    else:
        typer.echo("No GUI dist provided. API-only mode. Pass --gui-dist to serve the dashboard.")

    application = create_app(db_path=db_path, gui_dist=gui_dist, config_path=str(config.resolve()))
    uvicorn.run(application, host=host, port=port, reload=reload)



@app.command("reset-stuck")
def reset_stuck(
    db: Path = typer.Option("vuemorphic.db", "--db", help="Path to vuemorphic SQLite manifest DB."),
) -> None:
    """Reset all IN_PROGRESS nodes to NOT_STARTED.

    Run this before starting a new batch to clean up orphaned nodes left by a
    previous crash or reboot. Safe to run even if no nodes are stuck.
    """
    from vuemorphic.models.manifest import Manifest, NodeStatus

    db = db.resolve()
    if not db.exists():
        typer.echo(f"Error: {db} not found.", err=True)
        raise typer.Exit(1)

    manifest = Manifest.load(db)
    stuck = [
        nid for nid, n in manifest.nodes.items()
        if n.status == NodeStatus.IN_PROGRESS
    ]

    if not stuck:
        typer.echo("No stuck nodes found — nothing to reset.")
        return

    for nid in stuck:
        manifest.update_node(db, nid, status=NodeStatus.NOT_STARTED)

    typer.echo(f"Reset {len(stuck)} IN_PROGRESS node(s) to NOT_STARTED:")
    for nid in stuck[:10]:
        typer.echo(f"  {nid}")
    if len(stuck) > 10:
        typer.echo(f"  ... and {len(stuck) - 10} more")


if __name__ == "__main__":
    app()
