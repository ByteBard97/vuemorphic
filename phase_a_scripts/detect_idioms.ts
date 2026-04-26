/**
 * Phase A: detect React idioms in each component's source_text.
 * Updates idioms_needed in the manifest in-place.
 *
 * Idiom names map to classify_tiers.py _SONNET_IDIOMS and _OPUS_IDIOMS
 * and to idiom_dictionary.md section keys.
 */

import { Project, Node, SyntaxKind } from "ts-morph";
import * as fs from "fs";

const args = process.argv.slice(2);
function getArg(flag: string): string {
  const idx = args.indexOf(flag);
  if (idx === -1 || idx + 1 >= args.length) throw new Error(`Missing ${flag}`);
  return args[idx + 1];
}
const manifestPath = getArg("--manifest");
const manifest     = JSON.parse(fs.readFileSync(manifestPath, "utf8"));

type Detector = (n: Node, text: string) => boolean;

const REACT_HOOKS = [
  "useState", "useReducer", "useEffect", "useLayoutEffect", "useRef",
  "useCallback", "useMemo", "useContext", "useId", "useDeferredValue",
  "useTransition", "useSyncExternalStore",
];

const IDIOMS: Record<string, Detector> = {
  // State management hooks → ref/reactive decisions
  vue_reactivity_system: (_n, text) =>
    REACT_HOOKS.some((hook) => text.includes(`${hook}(`)),

  // Complex props (generic types, required/optional mix)
  prop_validation: (_n, text) =>
    /interface\s+\w+Props/.test(text) &&
    (/[<>]/.test(text.match(/interface\s+\w+Props[^}]+}/)?.[0] ?? "") ||
      (text.match(/interface\s+\w+Props[^}]+}/)?.[0] ?? "").split("\n").length > 6),

  // children prop or render props → slots conversion
  slots: (_n, text) =>
    /\bchildren\b/.test(text) ||
    /React\.ReactNode/.test(text) ||
    /\brender\w*\s*[=:]\s*\(/.test(text),

  // HOCs, compound components
  component_composition: (_n, text) =>
    /\bforwardRef\s*\(/.test(text) ||
    /\bDisplayName\b/.test(text) ||
    /\bwithRouter\b|\bwithStyles\b/.test(text),

  // CSS Modules or styled-components
  css_scoping: (_n, text) =>
    /styles\.\w+/.test(text) ||
    /styled\.\w+`/.test(text) ||
    /css`/.test(text),

  // Multiple useEffect or useEffect with cleanup
  lifecycle_hooks: (_n, text) => {
    const matches = text.match(/\buseEffect\s*\(/g);
    return (matches?.length ?? 0) > 1 ||
      (/\buseEffect\s*\(/.test(text) && /return\s+\(\s*\)\s*=>/.test(text));
  },

  // Context provider/consumer → provide/inject (opus-level)
  context_to_provide_inject: (_n, text) =>
    /\bcreateContext\s*\(/.test(text) ||
    /\buseContext\s*\(/.test(text),

  // forwardRef → defineExpose (opus-level)
  forward_ref: (_n, text) =>
    /\bforwardRef\s*[<(]/.test(text) ||
    /\buseImperativeHandle\s*\(/.test(text),

  // Render props / function-as-child → scoped slots (opus-level)
  named_children: (_n, text) =>
    /\bchildren\s*\(/.test(text) ||
    /\brender\w+\s*=\s*\{/.test(text) ||
    /typeof\s+children\s*===\s*['"]function['"]/.test(text),

  // Conditional rendering patterns
  conditional_rendering: (n, text) =>
    n.getDescendantsOfKind(SyntaxKind.ConditionalExpression).length > 0 ||
    /&&\s*</.test(text),

  // List rendering patterns
  list_rendering: (_n, text) =>
    /\.map\s*\(\s*(?:\(?\w+,?\s*\w*\)?\s*=>|\w+\s*=>)/.test(text) &&
    /</.test(text),

  // Event handling complexity
  event_handlers: (n, _text) =>
    n.getDescendantsOfKind(SyntaxKind.JsxAttribute).some((attr) => {
      const name = attr.getName();
      return name.startsWith("on") && name !== "onChange" && name !== "onClick";
    }),

  // Portal usage
  portals: (_n, text) =>
    /createPortal\s*\(/.test(text),

  // useReducer (more complex state than useState)
  reducer_to_reactive: (_n, text) =>
    /\buseReducer\s*\(/.test(text),

  // Async component / lazy loading
  async_component: (_n, text) =>
    /React\.lazy\s*\(/.test(text) || /import\s*\(/.test(text),

  // Design token globals usage
  claude_design_globals: (_n, text) =>
    /\bwfColors\b/.test(text) || /\bmfColors\b/.test(text) ||
    /\bwfFonts\b/.test(text) || /\bmfFonts\b/.test(text),

  // Import substitution needed (lucide-react → lucide-vue-next etc.)
  import_substitutions: (_n, text) =>
    /from\s+['"]lucide-react['"]/.test(text) ||
    /from\s+['"]framer-motion['"]/.test(text) ||
    /from\s+['"]react-hook-form['"]/.test(text) ||
    /from\s+['"]@radix-ui\//.test(text),

  // className pattern (easy but worth flagging)
  className_to_class: (_n, text) =>
    /\bclassName\s*=/.test(text),

  // Style object → :style binding
  style_binding: (_n, text) =>
    /\bstyle\s*=\s*\{\{/.test(text),

  // Icon objects (MfIcons, WfIcons, etc.) → v-html pattern
  icon_objects: (_n, text) =>
    /\b[A-Za-z]+Icons\b/.test(text),
};

// Build in-memory project
const project = new Project({ useInMemoryFileSystem: true });

for (const [nodeId, node] of Object.entries(manifest.nodes) as [string, any][]) {
  if (!node.source_text) continue;
  // Use .tsx extension so JSX syntax is accepted
  project.createSourceFile(`/${nodeId}.tsx`, node.source_text, { overwrite: true });
}

for (const [nodeId, node] of Object.entries(manifest.nodes) as [string, any][]) {
  const sf = project.getSourceFile(`/${nodeId}.tsx`);
  if (!sf) continue;
  const text = node.source_text as string;

  const idioms: string[] = [];
  for (const [name, detect] of Object.entries(IDIOMS)) {
    try {
      if (detect(sf, text)) idioms.push(name);
    } catch { /* skip */ }
  }
  manifest.nodes[nodeId].idioms_needed = idioms;
}

fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
console.log(`Idiom detection complete: ${manifestPath}`);
