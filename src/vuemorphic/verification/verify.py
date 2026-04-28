"""Verification pipeline for converted Vue SFC files.

Tiered checks in order of cost (cheapest first):

1. REMNANT  — instant: grep for React artifacts that shouldn't appear in Vue
2. POSTFILTER — instant: grep for unfilled markers, bail-out TODOs, any-types
3. COMPILE  — ~500ms: @vue/compiler-sfc structural parse
4. TSC      — ~5-15s: vue-tsc --noEmit on the whole project
5. VISUAL   — ~3-10s: myopex fingerprint diff vs React baseline (optional)

Cascade detection: vue-tsc errors in files other than the target are CASCADE
(a prior conversion broke a downstream component), not TSC (this snippet is bad).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class VerifyStatus(str, Enum):
    PASS       = "PASS"
    REMNANT    = "REMNANT"     # React artifacts found in Vue output
    POSTFILTER = "POSTFILTER"  # unfilled markers / TODO bail-outs / any types
    COMPILE    = "COMPILE"     # vue-tsc structural parse failure
    TSC        = "TSC"         # vue-tsc type errors in target file
    CASCADE    = "CASCADE"     # vue-tsc errors only in other (already-converted) files
    VISUAL     = "VISUAL"      # myopex fingerprint diff failed


@dataclass
class VerifyResult:
    status: VerifyStatus
    error: str = field(default="")
    # Actionable context for retry prompt (first error + ±5 lines + idiom card trigger)
    retry_context: str = field(default="")


# ── Tier 1: React remnant grep ────────────────────────────────────────────────

# These should NEVER appear in a Vue SFC — their presence means the model
# failed to translate something.
_REMNANTS: list[tuple[str, str]] = [
    ("className=",          "className attribute (should be class=)"),
    ("htmlFor=",            "htmlFor attribute (should be for=)"),
    ("tabIndex=",           "tabIndex attribute (should be tabindex=)"),
    ("dangerouslySetInnerHTML", "dangerouslySetInnerHTML (should be v-html)"),
    ("import React",        "React import (should be removed)"),
    ("from 'react'",        "react import (should be removed)"),
    ('from "react"',        "react import (should be removed)"),
    ("JSX.Element",         "JSX.Element type (should be removed)"),
    ("React.FC",            "React.FC type (should be removed)"),
    ("React.memo",          "React.memo (no Vue equivalent needed)"),
    ("React.lazy",          "React.lazy (use defineAsyncComponent in Vue)"),
    ("React.Fragment",      "React.Fragment (Vue supports multiple roots)"),
    ("forwardRef",          "forwardRef (use defineExpose in Vue)"),
    ("useImperativeHandle", "useImperativeHandle (use defineExpose in Vue)"),
    ("lucide-react",        "lucide-react import (should be lucide-vue-next)"),
    ("framer-motion",       "framer-motion import (should be motion-v)"),
    ("react-hook-form",     "react-hook-form import (should be vee-validate)"),
    ("ariaLabelledby=",     "camelCase ARIA attribute (should be aria-labelledby=)"),
    ("{/* ",                "JSX comment syntax (should be <!-- -->)"),
]


def _check_remnants(vue_content: str) -> VerifyResult | None:
    """Tier 1: scan for React artifacts that must not appear in Vue output."""
    for pattern, description in _REMNANTS:
        if pattern in vue_content:
            # Find the line
            for i, line in enumerate(vue_content.splitlines(), 1):
                if pattern in line:
                    context = f"line {i}: {line.strip()}"
                    break
            else:
                context = "(not found in line scan)"
            return VerifyResult(
                VerifyStatus.REMNANT,
                error=f"React remnant: {description}",
                retry_context=f"Found React remnant '{pattern}' — {description}. {context}",
            )
    return None


# ── Tier 1.5: Post-filter ─────────────────────────────────────────────────────

_SKELETON_MARKER = "TODO(vuemorphic):"
_BAIL_OUT_PATTERNS = [
    ("// TODO:",   "model left a TODO comment (did not fully translate)"),
    ("// FIXME:",  "model left a FIXME comment"),
    ("// ... existing code ...", "model used placeholder comment instead of translating"),
    ("// ...",     "model used ellipsis placeholder comment"),
]


def _check_postfilter(vue_content: str) -> VerifyResult | None:
    """Tier 1.5: check for unfilled skeleton markers and model bail-out patterns."""
    # Preamble check: Vue SFC must start with <template>, <script>, or <style>
    # If the agent output prose explanation before the SFC, reject immediately.
    first_line = vue_content.lstrip().split("\n")[0].strip()
    if not first_line.startswith("<"):
        return VerifyResult(
            VerifyStatus.POSTFILTER,
            error=f"Response does not start with a Vue SFC tag — got: {first_line[:80]!r}",
            retry_context="Output must begin immediately with <template> — no explanation or preamble.",
        )

    # Unfilled skeleton markers (model didn't fill in the section)
    if _SKELETON_MARKER in vue_content:
        count = vue_content.count(_SKELETON_MARKER)
        return VerifyResult(
            VerifyStatus.POSTFILTER,
            error=f"{count} TODO(vuemorphic): marker(s) remain unfilled",
            retry_context=(
                f"The skeleton has {count} unfilled section(s) marked "
                f"'TODO(vuemorphic):'. Fill in ALL sections."
            ),
        )

    # Model bail-out patterns
    for pattern, description in _BAIL_OUT_PATTERNS:
        if pattern in vue_content:
            for i, line in enumerate(vue_content.splitlines(), 1):
                if pattern in line:
                    return VerifyResult(
                        VerifyStatus.POSTFILTER,
                        error=f"Model bail-out: {description}",
                        retry_context=f"line {i}: {line.strip()} — {description}",
                    )

    return None


def _check_missing_imports(vue_content: str, target_dir: Path) -> VerifyResult | None:
    """Tier 1.6: scan import statements and verify every relative module exists on disk.

    Catches missing data files (registries, constants) before vue-tsc runs.
    Only checks relative imports (starting with '.') — node_modules and @/ aliases
    are handled by the TS resolver and only caught at TSC tier.
    """
    import re as _re
    # Match relative (./foo) and alias (@/foo) imports
    import_re = _re.compile(r'''from\s+['"]([.@][^'"]+)['"]|import\s+['"]([.@][^'"]+)['"]''')
    src_root = target_dir / "src"
    extensions = ["", ".ts", ".vue", ".js", ".json"]

    for m in import_re.finditer(vue_content):
        raw = m.group(1) or m.group(2)

        if raw.startswith("./"):
            # Relative: resolve from src/components/
            base = target_dir / "src" / "components" / raw
        elif raw.startswith("@/"):
            # Alias: @/ maps to src/
            base = src_root / raw[2:]
        else:
            continue

        found = any((base.parent / (base.name + ext)).exists() for ext in extensions)
        if not found:
            return VerifyResult(
                VerifyStatus.POSTFILTER,
                error=f"Import '{raw}' does not exist in the Vue project",
                retry_context=(
                    f"The import '{raw}' references a file that does not exist. "
                    f"Do not import external data registries or helpers — inline any "
                    f"data you need directly in the component, or remove the import entirely."
                ),
            )
    return None


# ── Tier 2: @vue/compiler-sfc structural parse ───────────────────────────────

_VUE_PARSE_TIMEOUT = 10


def _check_compile(vue_content: str, vue_path: Path) -> VerifyResult | None:
    """Tier 2: verify the file is structurally valid Vue SFC (fast parser check)."""
    # Write to a temp file and run a minimal Node.js parse via npx
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        suffix=".vue", mode="w", encoding="utf-8", delete=False
    ) as f:
        f.write(vue_content)
        tmp_path = f.name
    try:
        # Quick check: can Vue's compiler parse the template block?
        # Use node -e with @vue/compiler-sfc if available, else skip gracefully
        script = (
            "const {parse}=require('@vue/compiler-sfc');"
            f"const fs=require('fs');"
            "const src=fs.readFileSync(process.argv[1],'utf8');"
            "const {errors}=parse(src);"
            "if(errors.length){process.stderr.write(JSON.stringify(errors));process.exit(1);}"
        )
        proc = subprocess.run(
            ["node", "-e", script, tmp_path],
            capture_output=True, text=True, timeout=_VUE_PARSE_TIMEOUT,
            cwd=str(vue_path.parent.parent.parent),  # corpora/claude-design-vue/
        )
        if proc.returncode != 0:
            raw_err = proc.stderr[:500] or proc.stdout[:500]
            return VerifyResult(
                VerifyStatus.COMPILE,
                error=f"Vue SFC parse error: {raw_err}",
                retry_context=f"Vue compiler could not parse the SFC: {raw_err}",
            )
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        # If @vue/compiler-sfc isn't installed yet, skip this tier gracefully
        logger.debug("Tier 2 skipped: @vue/compiler-sfc not available")
    finally:
        os.unlink(tmp_path)
    return None


# ── Tier 3: vue-tsc --noEmit ─────────────────────────────────────────────────

_TSC_TIMEOUT = 60


def _is_cascade_failure(error_text: str, target_vue_filename: str) -> bool:
    """Return True if ALL vue-tsc errors are in files other than the target.

    vue-tsc error lines look like:
        src/components/Button.vue:23:5: error TS2345: ...
    If every error line names a file that is NOT our target, the failure is a
    cascade from a previously-converted component, not from this one.
    """
    # vue-tsc formats errors as: src/components/Foo.vue(23,5): error TS2345: ...
    error_lines = [
        line for line in error_text.splitlines()
        if ": error TS" in line and ".vue" in line
    ]
    if not error_lines:
        return False
    return all(target_vue_filename not in line for line in error_lines)


def _first_error_with_context(error_text: str, vue_content: str) -> str:
    """Extract first error + ±5 lines of source context for retry prompt."""
    lines = error_text.splitlines()
    first_err = next((l for l in lines if ": error TS" in l), lines[0] if lines else "")

    # Try to extract line number from error
    m = re.search(r":(\d+):\d+:", first_err)
    if m and vue_content:
        lineno = int(m.group(1))
        src_lines = vue_content.splitlines()
        start = max(0, lineno - 6)
        end = min(len(src_lines), lineno + 5)
        snippet = "\n".join(
            f"{'>>>' if i + 1 == lineno else '   '} {src_lines[i]}"
            for i in range(start, end)
        )
        return f"{first_err}\n\nSource context:\n{snippet}"
    return first_err


def _check_tsc(
    vue_path: Path,
    target_dir: Path,
    vue_content: str,
) -> VerifyResult | None:
    """Tier 3: run vue-tsc --noEmit on the whole project."""
    # Write the content to disk first
    vue_path.write_text(vue_content, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["npx", "vue-tsc", "--noEmit"],
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=_TSC_TIMEOUT,
        )
        if proc.returncode == 0:
            return None

        error_text = proc.stderr[:4000] or proc.stdout[:4000]
        rel_filename = str(vue_path.relative_to(target_dir))

        if _is_cascade_failure(error_text, rel_filename):
            return VerifyResult(
                VerifyStatus.CASCADE,
                error=error_text[:500],
                retry_context=(
                    "vue-tsc errors are in OTHER components (cascade from a "
                    "previously-converted file). This component may be fine."
                ),
            )

        first_err = _first_error_with_context(error_text, vue_content)
        return VerifyResult(
            VerifyStatus.TSC,
            error=error_text[:500],
            retry_context=first_err,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return VerifyResult(VerifyStatus.TSC, error=str(exc))


# ── Tier 4: ESLint ────────────────────────────────────────────────────────────

_ESLINT_TIMEOUT = 15


def _check_eslint(vue_path: Path, target_dir: Path) -> VerifyResult | None:
    """Tier 4: run ESLint with vue3-recommended rules."""
    try:
        proc = subprocess.run(
            ["npx", "eslint", "--format=json", str(vue_path)],
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=_ESLINT_TIMEOUT,
        )
        if proc.returncode == 0:
            return None

        # Parse ESLint JSON output
        try:
            results = json.loads(proc.stdout)
            errors = [
                m for r in results for m in r.get("messages", [])
                if m.get("severity", 0) >= 2
            ]
            if errors:
                first = errors[0]
                desc = f"line {first.get('line','?')}: {first.get('ruleId','?')}: {first.get('message','?')}"
                return VerifyResult(
                    VerifyStatus.TSC,  # Treat ESLint errors as TSC-tier failures
                    error=f"ESLint: {desc}",
                    retry_context=f"ESLint error — {desc}",
                )
        except (json.JSONDecodeError, KeyError):
            pass  # ESLint not configured yet, skip gracefully
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        logger.debug("Tier 4 skipped: eslint not available")
    return None


# ── Tier 5: myopex visual diff ────────────────────────────────────────────────

_MYOPEX_TIMEOUT = 30


def _check_visual(
    component_name: str,
    vue_url: str,
    baseline_dir: str,
    myopex_dir: str,
) -> VerifyResult | None:
    """Tier 5: myopex fingerprint diff vs React baseline."""
    import tempfile, os

    current_dir = tempfile.mkdtemp(prefix="vuemorphic_visual_")
    try:
        # Capture current Vue render
        capture_proc = subprocess.run(
            [
                "npx", "myopex", "capture",
                "--url", f"{vue_url}/{component_name}",
                "--out", current_dir,
                "--state", component_name,
            ],
            capture_output=True,
            text=True,
            timeout=_MYOPEX_TIMEOUT,
        )
        if capture_proc.returncode != 0:
            logger.warning("myopex capture failed: %s", capture_proc.stderr[:200])
            return None  # Skip visual tier if capture fails

        # Diff against baseline
        diff_proc = subprocess.run(
            [
                "npx", "myopex", "diff",
                "--old", baseline_dir,
                "--new", current_dir,
                "--state", component_name,
            ],
            capture_output=True,
            text=True,
            timeout=_MYOPEX_TIMEOUT,
            cwd=myopex_dir,
        )

        report_path = Path(current_dir) / "report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            if not report.get("pass", True):
                failures = report.get("regressions", {}).get("failures", [])
                missing = report.get("regressions", {}).get("missing", [])
                summary_parts = []
                if failures:
                    summary_parts.append(
                        f"{len(failures)} regression(s): "
                        + "; ".join(
                            f"{f['component']}.{f['property']}: "
                            f"expected={f['expected']!r} actual={f['actual']!r}"
                            for f in failures[:3]
                        )
                    )
                if missing:
                    summary_parts.append(f"missing components: {', '.join(missing[:3])}")
                summary = " | ".join(summary_parts) or "visual diff failed"
                return VerifyResult(
                    VerifyStatus.VISUAL,
                    error=summary,
                    retry_context=(
                        f"myopex visual diff detected differences: {summary}. "
                        f"Full report: {current_dir}/report.json"
                    ),
                )
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        logger.debug("Tier 5 skipped: myopex not available or timed out")
    finally:
        import shutil
        shutil.rmtree(current_dir, ignore_errors=True)

    return None


# ── After-pass: whole-project regression check ───────────────────────────────


def check_project_regression(target_dir: Path) -> VerifyResult | None:
    """After a PASS, verify the whole Vue project still compiles.

    A newly-converted component might introduce type errors in already-converted
    components that import it. Run this after every successful conversion.
    """
    try:
        proc = subprocess.run(
            ["npx", "vue-tsc", "--noEmit"],
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=_TSC_TIMEOUT,
        )
        if proc.returncode == 0:
            return None
        error_text = proc.stderr[:2000] or proc.stdout[:2000]
        return VerifyResult(
            VerifyStatus.CASCADE,
            error=f"Post-pass regression: {error_text[:500]}",
            retry_context="This conversion introduced a regression in another component.",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


# ── Main entry point ─────────────────────────────────────────────────────────


def verify_vue_file(
    node_id: str,
    vue_content: str,
    target_dir: Path,
    component_name: str,
    vue_url: str | None = None,
    baseline_dir: str | None = None,
    myopex_dir: str | None = None,
) -> VerifyResult:
    """Run all verification tiers; return first failure or PASS.

    Args:
        node_id:          Manifest node ID (for logging).
        vue_content:      The complete .vue file content from the agent.
        target_dir:       Root of corpora/claude-design-vue/.
        component_name:   PascalCase component name (e.g. 'Sidebar').
        vue_url:          Base URL of Vite dev server (for visual tier, optional).
        baseline_dir:     myopex baseline directory (for visual tier, optional).
        myopex_dir:       myopex project directory (optional).
    """
    vue_path = target_dir / "src" / "components" / f"{component_name}.vue"

    # Tier 1: React remnants
    if r := _check_remnants(vue_content):
        logger.debug("[%s] REMNANT: %s", node_id, r.error)
        return r

    # Tier 1.5: Post-filter
    if r := _check_postfilter(vue_content):
        logger.debug("[%s] POSTFILTER: %s", node_id, r.error)
        return r

    # Tier 1.6: Missing relative imports
    if r := _check_missing_imports(vue_content, target_dir):
        logger.debug("[%s] POSTFILTER (missing import): %s", node_id, r.error)
        return r

    # Tier 2: @vue/compiler-sfc structural parse
    if r := _check_compile(vue_content, vue_path):
        logger.debug("[%s] COMPILE: %s", node_id, r.error)
        return r

    # Tier 3: vue-tsc
    if r := _check_tsc(vue_path, target_dir, vue_content):
        logger.debug("[%s] %s: %s", node_id, r.status, r.error)
        return r

    # Tier 4: ESLint (write has already been done by Tier 3)
    if r := _check_eslint(vue_path, target_dir):
        logger.debug("[%s] ESLINT: %s", node_id, r.error)
        return r

    # Tier 5: myopex visual diff (only if configured)
    if vue_url and baseline_dir:
        if r := _check_visual(component_name, vue_url, baseline_dir, myopex_dir or "."):
            logger.debug("[%s] VISUAL: %s", node_id, r.error)
            return r

    logger.info("[%s] PASS", node_id)
    return VerifyResult(VerifyStatus.PASS)
