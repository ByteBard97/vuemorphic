/**
 * Phase A: extract ComponentContract per React component from .jsx/.tsx files.
 *
 * Key behaviors:
 * - One source file may produce N manifest nodes (one per named component).
 * - Only PascalCase named components become nodes. Anonymous inline JSX stays inline.
 * - Fails loudly on name collisions across source files (no silent rename in v0).
 * - Outputs ComponentContract fields alongside standard manifest fields.
 */

import {
  Project, SourceFile, Node, SyntaxKind, ts, FunctionDeclaration, VariableStatement,
} from "ts-morph";
import * as path from "path";
import * as fs from "fs";

// ── CLI args ─────────────────────────────────────────────────────────────────

const args = process.argv.slice(2);
function getArg(flag: string): string {
  const idx = args.indexOf(flag);
  if (idx === -1 || idx + 1 >= args.length) throw new Error(`Missing ${flag}`);
  return args[idx + 1];
}
const tsconfigPath = path.resolve(getArg("--tsconfig"));
const sourceRoot   = path.resolve(getArg("--source-root"));
const outPath      = getArg("--out");

// ── Helpers ──────────────────────────────────────────────────────────────────

function isPascalCase(name: string): boolean {
  return /^[A-Z][a-zA-Z0-9]*$/.test(name);
}

function relPath(sf: SourceFile): string {
  return path.relative(sourceRoot, sf.getFilePath());
}

function complexity(root: Node): number {
  let n = 1;
  root.forEachDescendant((d) => {
    switch (d.getKind()) {
      case SyntaxKind.IfStatement:
      case SyntaxKind.ConditionalExpression:
      case SyntaxKind.CaseClause:
      case SyntaxKind.WhileStatement:
      case SyntaxKind.ForStatement:
      case SyntaxKind.ForInStatement:
      case SyntaxKind.ForOfStatement:
      case SyntaxKind.CatchClause:
        n++; break;
      case SyntaxKind.BinaryExpression: {
        const op = (d as any).getOperatorToken().getKind();
        if (
          op === SyntaxKind.AmpersandAmpersandToken ||
          op === SyntaxKind.BarBarToken ||
          op === SyntaxKind.QuestionQuestionToken
        ) n++;
        break;
      }
    }
  });
  return n;
}

// ── ComponentContract extraction ─────────────────────────────────────────────

const SHADCN_PACKAGES = new Set([
  "button", "card", "dialog", "dropdown-menu", "input", "label",
  "select", "separator", "sheet", "switch", "tabs", "textarea", "tooltip",
  "badge", "avatar", "checkbox", "radio-group", "slider", "toggle",
  "accordion", "alert", "alert-dialog", "popover", "command", "scroll-area",
]);

const DESIGN_TOKEN_NAMES = new Set(["wfColors", "mfColors", "wfFonts", "mfFonts"]);

function extractContract(componentNode: Node, name: string): Record<string, any> {
  const text = componentNode.getFullText();

  // Props interface: look for interface <Name>Props or type <Name>Props
  const propsInterfaceMatch = text.match(
    new RegExp(`(?:interface|type)\\s+${name}Props\\b[\\s\\S]*?(?=\\n(?:interface|type|function|const|export|$))`)
  );
  const propsInterface = propsInterfaceMatch ? propsInterfaceMatch[0].trim() : "";

  // Emitted events: callback props named onX → event name x
  const emittedEvents: string[] = [];
  const onPropRe = /\bon([A-Z][a-zA-Z]*)\s*[?:]?\s*[:(]/g;
  let m: RegExpExecArray | null;
  while ((m = onPropRe.exec(propsInterface || text)) !== null) {
    const eventName = m[1].charAt(0).toLowerCase() + m[1].slice(1);
    if (!emittedEvents.includes(eventName)) emittedEvents.push(eventName);
  }

  // Child components: PascalCase JSX tags used in the return value
  const childComponents: string[] = [];
  const jsxTagRe = /<([A-Z][a-zA-Z0-9]*)/g;
  while ((m = jsxTagRe.exec(text)) !== null) {
    const tag = m[1];
    if (tag !== name && !childComponents.includes(tag)) childComponents.push(tag);
  }

  // Icon imports from lucide-react
  const iconImports: string[] = [];
  const iconImportRe = /import\s*\{([^}]+)\}\s*from\s*['"]lucide-react['"]/g;
  while ((m = iconImportRe.exec(text)) !== null) {
    m[1].split(",").forEach((s) => {
      const icon = s.trim();
      if (icon && !iconImports.includes(icon)) iconImports.push(icon);
    });
  }

  // shadcn imports
  const shadcnImports: string[] = [];
  const shadcnRe = /import\s*\{([^}]+)\}\s*from\s*['"]@\/components\/ui\/([^'"]+)['"]/g;
  while ((m = shadcnRe.exec(text)) !== null) {
    const component = m[2];
    if (SHADCN_PACKAGES.has(component)) {
      m[1].split(",").forEach((s) => {
        const cmp = s.trim();
        if (cmp && !shadcnImports.includes(cmp)) shadcnImports.push(cmp);
      });
    }
  }

  // Design tokens reference
  let referencesDesignTokens = false;
  for (const token of DESIGN_TOKEN_NAMES) {
    if (text.includes(token)) {
      referencesDesignTokens = true;
      break;
    }
  }

  // children prop → needs <slot />
  const hasChildrenProp =
    /\bchildren\b/.test(propsInterface) ||
    /\{children\}/.test(text) ||
    /React\.ReactNode/.test(propsInterface);

  return {
    props_interface: propsInterface,
    emitted_events: emittedEvents,
    child_components: childComponents,
    icon_imports: iconImports,
    shadcn_imports: shadcnImports,
    references_design_tokens: referencesDesignTokens,
    has_children_prop: hasChildrenProp,
  };
}

// ── Component discovery ───────────────────────────────────────────────────────

interface ComponentDef {
  name: string;
  node: Node;
  sf: SourceFile;
}

function findComponents(sf: SourceFile): ComponentDef[] {
  const results: ComponentDef[] = [];

  // 1. Named function declarations: function Foo(...) { return <...> }
  for (const fn of sf.getFunctions()) {
    const name = fn.getName();
    if (!name || !isPascalCase(name)) continue;
    // Heuristic: contains JSX (either <tag or JSX.Element return type)
    const text = fn.getFullText();
    if (/<[A-Za-z]/.test(text) || /JSX\.Element|React\.ReactNode/.test(text)) {
      results.push({ name, node: fn, sf });
    }
  }

  // 2. Const arrow functions: const Foo = (...) => <...> or const Foo: React.FC = ...
  for (const varStmt of sf.getVariableStatements()) {
    for (const decl of varStmt.getDeclarationList().getDeclarations()) {
      const name = decl.getName();
      if (!name || !isPascalCase(name)) continue;
      const init = decl.getInitializer();
      if (!init) continue;
      const kind = init.getKind();
      if (
        kind !== SyntaxKind.ArrowFunction &&
        kind !== SyntaxKind.FunctionExpression
      ) continue;
      const text = init.getFullText();
      if (/<[A-Za-z]/.test(text) || /JSX\.Element|React\.ReactNode/.test(text)) {
        results.push({ name, node: decl.getParent()?.getParent() ?? decl, sf });
      }
    }
  }

  return results;
}

// ── Main ──────────────────────────────────────────────────────────────────────

const project = new Project({
  tsConfigFilePath: tsconfigPath,
  skipAddingFilesFromTsConfig: true,  // we add source files explicitly below
});
// Add all JS/JSX/TS/TSX files from the source root so tsconfig location doesn't matter
project.addSourceFilesAtPaths([
  path.join(sourceRoot, "**/*.ts"),
  path.join(sourceRoot, "**/*.tsx"),
  path.join(sourceRoot, "**/*.js"),
  path.join(sourceRoot, "**/*.jsx"),
]);
const resultNodes: Record<string, any> = {};

// Collision detection: component name → first source file that defines it
const nameToSourceFile = new Map<string, string>();

for (const sf of project.getSourceFiles()) {
  const rel = relPath(sf);
  const components = findComponents(sf);

  for (const { name, node } of components) {
    // Fail loudly on name collision
    if (nameToSourceFile.has(name)) {
      const existing = nameToSourceFile.get(name)!;
      if (existing !== rel) {
        throw new Error(
          `Name collision: component "${name}" defined in both "${existing}" and "${rel}". ` +
          `Prefix with source filename or resolve before running Phase A.`
        );
      }
      continue; // same file, skip duplicate
    }
    nameToSourceFile.set(name, rel);

    const contract = extractContract(node, name);

    const manifestNode: Record<string, any> = {
      node_id: name,
      source_file: rel,
      line_start: node.getStartLineNumber(),
      line_end: node.getEndLineNumber(),
      source_text: node.getText(),
      node_kind: "react_component",
      // ComponentContract fields
      props_interface: contract.props_interface,
      emitted_events: contract.emitted_events,
      child_components: contract.child_components,
      icon_imports: contract.icon_imports,
      shadcn_imports: contract.shadcn_imports,
      references_design_tokens: contract.references_design_tokens,
      has_children_prop: contract.has_children_prop,
      // Dependency graph (child_components resolved at pass-2 below)
      type_dependencies: [],
      call_dependencies: contract.child_components.slice(),
      callers: [],
      // Metrics
      cyclomatic_complexity: complexity(node),
      idioms_needed: [],
      // Translation state
      topological_order: null,
      bfs_level: null,
      tier: null,
      tier_reason: null,
      status: "not_started",
      snippet_path: null,
      attempt_count: 0,
      last_error: null,
    };

    resultNodes[name] = manifestNode;
  }
}

// Pass 2: filter call_dependencies to only nodes that exist in the manifest
// (child_components may reference components not in this corpus)
for (const node of Object.values(resultNodes)) {
  node.call_dependencies = (node.call_dependencies as string[]).filter(
    (dep: string) => dep in resultNodes && dep !== node.node_id
  );
}

const manifest = {
  version: "1.0",
  source_repo: sourceRoot,
  generated_at: new Date().toISOString(),
  nodes: resultNodes,
};

fs.writeFileSync(outPath, JSON.stringify(manifest, null, 2));
console.log(
  `Wrote manifest: ${outPath} (${Object.keys(resultNodes).length} components from ${project.getSourceFiles().length} source files)`
);
