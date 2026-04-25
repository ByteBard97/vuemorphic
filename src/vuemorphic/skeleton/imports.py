"""Import substitution tables for the Vue skeleton builder."""

from __future__ import annotations

# React hooks → Vue Composition API symbols
HOOK_TO_VUE: dict[str, list[str]] = {
    "useState":            ["ref"],
    "useEffect":           ["watch", "onMounted"],
    "useMemo":             ["computed"],
    "useCallback":         [],          # plain function in Vue
    "useRef":              ["ref"],
    "useContext":          ["inject"],
    "useReducer":          ["ref"],
    "useLayoutEffect":     ["onMounted"],
    "useImperativeHandle": [],
    "useId":               [],
}

# lucide-react icon package → Vue equivalent
ICON_PACKAGE_MAP: dict[str, str] = {
    "lucide-react":  "lucide-vue-next",
    "react-icons":   "@iconify/vue",
}

# shadcn/ui component name → shadcn-vue import path
SHADCN_COMPONENT_MAP: dict[str, str] = {
    "Button":       "@/components/ui/button",
    "Input":        "@/components/ui/input",
    "Textarea":     "@/components/ui/textarea",
    "Select":       "@/components/ui/select",
    "Checkbox":     "@/components/ui/checkbox",
    "Switch":       "@/components/ui/switch",
    "Slider":       "@/components/ui/slider",
    "Label":        "@/components/ui/label",
    "Card":         "@/components/ui/card",
    "CardHeader":   "@/components/ui/card",
    "CardContent":  "@/components/ui/card",
    "CardFooter":   "@/components/ui/card",
    "Dialog":       "@/components/ui/dialog",
    "Sheet":        "@/components/ui/sheet",
    "Popover":      "@/components/ui/popover",
    "Tooltip":      "@/components/ui/tooltip",
    "Badge":        "@/components/ui/badge",
    "Separator":    "@/components/ui/separator",
    "ScrollArea":   "@/components/ui/scroll-area",
    "Tabs":         "@/components/ui/tabs",
    "TabsList":     "@/components/ui/tabs",
    "TabsTrigger":  "@/components/ui/tabs",
    "TabsContent":  "@/components/ui/tabs",
    "DropdownMenu": "@/components/ui/dropdown-menu",
    "Avatar":       "@/components/ui/avatar",
    "Progress":     "@/components/ui/progress",
    "Skeleton":     "@/components/ui/skeleton",
    "Table":        "@/components/ui/table",
    "Toast":        "@/components/ui/toast",
    "Alert":        "@/components/ui/alert",
    "Command":      "@/components/ui/command",
}

# Package-level library substitutions (full package name)
PACKAGE_SUBSTITUTIONS: dict[str, str] = {
    "react":                "vue",
    "react-dom":            "vue",
    "lucide-react":         "lucide-vue-next",
    "framer-motion":        "motion-v",
    "react-hook-form":      "vee-validate",
    "@radix-ui/react-icons": "lucide-vue-next",
}


def build_icon_import_line(icon_names: list[str]) -> str:
    """Generate the lucide-vue-next import line for given icon names."""
    if not icon_names:
        return ""
    names = ", ".join(sorted(set(icon_names)))
    return f"import {{ {names} }} from 'lucide-vue-next'"


def build_shadcn_import_lines(shadcn_names: list[str]) -> list[str]:
    """Generate per-path import lines for shadcn-vue components."""
    by_path: dict[str, list[str]] = {}
    for name in shadcn_names:
        path = SHADCN_COMPONENT_MAP.get(name, f"@/components/ui/{name.lower()}")
        by_path.setdefault(path, []).append(name)
    lines = []
    for path, names in sorted(by_path.items()):
        names_str = ", ".join(sorted(names))
        lines.append(f"import {{ {names_str} }} from '{path}'")
    return lines
