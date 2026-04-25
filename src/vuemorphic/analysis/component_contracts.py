"""Phase A: extract ComponentContracts from React source files.

Design notes
------------
- Pure regex + string analysis (no ts-morph from Python).
- Source corpus is Flora CAD v2: plain .jsx, no TypeScript interfaces.
  Props interfaces are synthesised from inline destructuring + default inference.
- One source file can yield many components; returns one contract per component.
- node_id collision: if two files export the same name the id is
  "{file_stem}.{ComponentName}".
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from vuemorphic.models.contract import ComponentContract
from vuemorphic.skeleton.imports import HOOK_TO_VUE

logger = logging.getLogger(__name__)

# ── Regexes ───────────────────────────────────────────────────────────────────

_PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

# function ComponentName({ ... }) or function ComponentName(props)
_FUNC_DECL_RE = re.compile(
    r"^function\s+([A-Z][A-Za-z0-9]*)[\s\n]*\(", re.MULTILINE
)
# const ComponentName = ({ ... }) =>  or  const ComponentName = (props) =>
_ARROW_DECL_RE = re.compile(
    r"^(?:export\s+)?const\s+([A-Z][A-Za-z0-9]*)\s*=\s*[\s\n]*\(", re.MULTILINE
)

_TOKEN_RE = re.compile(r"\b(wfColors|mfColors|wfFonts|mfFonts)\b")
_LUCIDE_RE = re.compile(r"import\s*\{([^}]+)\}\s*from\s*['\"]lucide-react['\"]")
_SHADCN_RE = re.compile(
    r"import\s*\{([^}]+)\}\s*from\s*['\"]@/components/ui/[^'\"]+['\"]"
)
_CHILDREN_RE = re.compile(r"\bchildren\b")
_HOOKS_RE = re.compile(
    r"\b(useState|useEffect|useMemo|useCallback|useRef|useContext|"
    r"useReducer|useLayoutEffect|useImperativeHandle|useId)\b"
)
# Callback props: onX or props typed as () => void / (arg) => void
_CALLBACK_PROP_RE = re.compile(r"\bon([A-Z][A-Za-z0-9]*)\b")

# ── Vite / Vue project scaffold templates ────────────────────────────────────

_VITE_CONFIG_TS = """\
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
})
"""

_TSCONFIG_JSON: dict[str, Any] = {
    "compilerOptions": {
        "target": "ESNext",
        "module": "ESNext",
        "moduleResolution": "Bundler",
        "lib": ["ESNext", "DOM"],
        "jsx": "preserve",
        "strict": True,
        "noEmit": True,
        "skipLibCheck": True,
        "baseUrl": ".",
        "paths": {"@/*": ["./src/*"]},
    },
    "include": ["src/**/*.ts", "src/**/*.vue"],
}


# ── Public API ────────────────────────────────────────────────────────────────


def extract_contracts(
    source_dir: str, config: dict[str, Any]
) -> list[ComponentContract]:
    """Parse all .jsx/.tsx files under source_dir; return one contract per component."""
    root = Path(source_dir)
    if not root.exists():
        raise FileNotFoundError(f"source_dir does not exist: {root}")

    source_files = sorted(root.rglob("*.jsx")) + sorted(root.rglob("*.tsx"))
    if not source_files:
        logger.warning("No .jsx/.tsx files found under %s", root)

    # First pass: collect (name, path, text) to detect collisions
    raw: list[tuple[str, Path, str]] = []
    for path in source_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            continue
        for comp_name in _find_component_names(text):
            raw.append((comp_name, path, text))

    # Detect name collisions across files
    name_counts: dict[str, int] = {}
    for name, _, _ in raw:
        name_counts[name] = name_counts.get(name, 0) + 1

    all_names = {name for name, _, _ in raw}

    contracts: list[ComponentContract] = []
    for name, path, text in raw:
        stem = path.stem
        rel = str(path.relative_to(root))
        node_id = f"{stem}.{name}" if name_counts[name] > 1 else name
        contract = _build_contract(
            node_id=node_id,
            component_name=name,
            source_file=rel,
            source_text=text,
            all_component_names=all_names,
        )
        contracts.append(contract)

    contracts.sort(key=lambda c: (c.source_file, c.component_name))
    logger.info(
        "Extracted %d contracts from %d files", len(contracts), len(source_files)
    )
    return contracts


def setup_vue_project(target_dir: str, config: dict[str, Any]) -> None:
    """Write the Vue 3 + Vite scaffold to target_dir (once, idempotent)."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "src").mkdir(exist_ok=True)
    (target / "src" / "components").mkdir(exist_ok=True)

    _write_if_missing(
        target / "package.json", _build_package_json(config)
    )
    _write_if_missing(
        target / "tsconfig.json", json.dumps(_TSCONFIG_JSON, indent=2) + "\n"
    )
    _write_if_missing(target / "vite.config.ts", _VITE_CONFIG_TS)

    tokens_path = target / "src" / "design-tokens.ts"
    if not tokens_path.exists():
        source_root = Path(config.get("source_repo", "."))
        tokens_ts = _extract_design_tokens(source_root)
        tokens_path.write_text(tokens_ts, encoding="utf-8")
        logger.info("Wrote design-tokens.ts (%d bytes)", len(tokens_ts))


# ── Internals ─────────────────────────────────────────────────────────────────


def _find_component_names(source_text: str) -> list[str]:
    """Return PascalCase component names defined in source_text."""
    names: list[str] = []
    for m in _FUNC_DECL_RE.finditer(source_text):
        names.append(m.group(1))
    for m in _ARROW_DECL_RE.finditer(source_text):
        names.append(m.group(1))
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _build_contract(
    node_id: str,
    component_name: str,
    source_file: str,
    source_text: str,
    all_component_names: set[str],
) -> ComponentContract:
    """Build a ComponentContract from source text analysis."""
    # Vue imports from detected React hooks
    hooks = set(_HOOKS_RE.findall(source_text))
    vue_symbols: list[str] = []
    for hook in sorted(hooks):
        vue_symbols.extend(HOOK_TO_VUE.get(hook, []))
    vue_imports = sorted(set(vue_symbols)) or ["ref"]

    # Icon imports
    icon_imports: list[str] = []
    for m in _LUCIDE_RE.finditer(source_text):
        names = [n.strip() for n in m.group(1).split(",") if n.strip()]
        icon_imports.extend(names)

    # shadcn imports
    shadcn_imports: list[str] = []
    for m in _SHADCN_RE.finditer(source_text):
        names = [n.strip() for n in m.group(1).split(",") if n.strip()]
        shadcn_imports.extend(names)

    # Child components (PascalCase JSX tags used in the file)
    child_components = [
        name for name in all_component_names
        if name != component_name and re.search(rf"<{re.escape(name)}[\s/>]", source_text)
    ]

    # Callback props → emitted events
    emitted_events = []
    for m in _CALLBACK_PROP_RE.finditer(source_text):
        event_name = m.group(1)[0].lower() + m.group(1)[1:]
        if event_name not in emitted_events:
            emitted_events.append(event_name)

    # Synthesise props interface from destructuring
    props_interface, prop_defaults = _synthesise_props(
        component_name, source_text
    )

    return ComponentContract(
        node_id=node_id,
        component_name=component_name,
        source_file=source_file,
        props_interface=props_interface,
        prop_defaults=prop_defaults,
        emitted_events=emitted_events,
        child_components=child_components,
        vue_imports=vue_imports,
        icon_imports=list(dict.fromkeys(icon_imports)),
        shadcn_imports=list(dict.fromkeys(shadcn_imports)),
        references_design_tokens=bool(_TOKEN_RE.search(source_text)),
        has_children_prop=bool(_CHILDREN_RE.search(source_text)),
    )


def _synthesise_props(
    component_name: str, source_text: str
) -> tuple[str, dict[str, str]]:
    """Synthesise a TypeScript Props interface from destructuring patterns.

    For plain .jsx files (no TS interface), we scan the component's parameter
    list for destructured prop names and default values.

    Returns (interface_text, defaults_dict).
    """
    # Try to find the component's parameter destructuring
    # e.g. function Sidebar({ items, onSelect, w = 1100, h = 900 })
    pattern = re.compile(
        rf"(?:function\s+{re.escape(component_name)}\s*|"
        rf"const\s+{re.escape(component_name)}\s*=\s*)"
        r"\(\s*\{([^}]*)\}"
    )
    m = pattern.search(source_text)
    if not m:
        return "", {}

    raw_params = m.group(1)
    prop_lines: list[str] = []
    defaults: dict[str, str] = {}

    for param in raw_params.split(","):
        param = param.strip()
        if not param:
            continue
        if "=" in param:
            name, default = param.split("=", 1)
            name = name.strip()
            default = default.strip()
            defaults[name] = default
            # Infer type from default
            ts_type = _infer_type(default)
        else:
            name = param.strip()
            # Infer type from name conventions
            ts_type = _infer_type_from_name(name)

        # Skip callback props (onX) — they become emits, not props
        if re.match(r"^on[A-Z]", name):
            continue

        prop_lines.append(f"  {name}: {ts_type}")

    if not prop_lines:
        return "", {}

    interface_text = (
        f"interface {component_name}Props {{\n"
        + "\n".join(prop_lines)
        + "\n}"
    )
    return interface_text, defaults


def _infer_type(default_value: str) -> str:
    """Infer TypeScript type from a default value string."""
    v = default_value.strip()
    if v in ("true", "false"):
        return "boolean"
    if v.startswith(("'", '"', "`")):
        return "string"
    if re.match(r"^-?\d+(\.\d+)?$", v):
        return "number"
    if v in ("{}", "[]"):
        return "Record<string, unknown>"
    if v == "null" or v == "undefined":
        return "unknown"
    return "unknown"


def _infer_type_from_name(name: str) -> str:
    """Infer TypeScript type from prop name conventions."""
    lower = name.lower()
    if lower in ("w", "h", "width", "height", "size", "count", "index", "x", "y"):
        return "number"
    if lower in ("label", "title", "text", "name", "id", "className", "class"):
        return "string"
    if lower in ("visible", "open", "active", "disabled", "checked", "selected"):
        return "boolean"
    if lower in ("children", "content", "header", "footer"):
        return "unknown"
    if lower.endswith("s") or lower in ("items", "options", "data", "list"):
        return "unknown[]"
    return "unknown"


def _extract_design_tokens(source_root: Path) -> str:
    """Extract wfColors, wfFonts, mfColors, mfFonts from source files."""
    lines: list[str] = [
        "// Auto-generated by vuemorphic Phase A",
        "// Source: Flora CAD v2 design token files",
        "",
    ]

    token_files = list(source_root.glob("*.jsx")) + list(source_root.glob("*.tsx"))
    found: dict[str, str] = {}

    for path in token_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for token_name in ["wfColors", "wfFonts", "mfColors", "mfFonts"]:
            if token_name in found:
                continue
            m = re.search(
                rf"(?:const|var|let)\s+{re.escape(token_name)}\s*=\s*(\{{[^;]+\})",
                text,
                re.DOTALL,
            )
            if m:
                found[token_name] = m.group(1).strip()

    for name, value in found.items():
        # Convert JS object literal to TypeScript export
        ts_value = value.replace("//", "//")
        lines.append(f"export const {name} = {ts_value} as const")
        lines.append("")

    if not found:
        lines.append("// No design tokens found in source — add manually")
        lines.append("export const wfColors = {} as const")
        lines.append("export const wfFonts = {} as const")
        lines.append("export const mfColors = {} as const")
        lines.append("export const mfFonts = {} as const")

    return "\n".join(lines) + "\n"


def _build_package_json(config: dict[str, Any]) -> str:
    pkg = {
        "name": "claude-design-vue",
        "version": "0.0.1",
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "vue-tsc && vite build",
            "typecheck": "vue-tsc --noEmit",
        },
        "dependencies": {
            "vue": "^3.4.0",
            "pinia": "^2.1.0",
            "@vueuse/core": "^10.0.0",
            "lucide-vue-next": "^0.460.0",
        },
        "devDependencies": {
            "@vitejs/plugin-vue": "^5.0.0",
            "vite": "^5.0.0",
            "typescript": "^5.4.0",
            "vue-tsc": "^2.0.0",
        },
    }
    return json.dumps(pkg, indent=2) + "\n"


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        logger.debug("Wrote %s", path)
