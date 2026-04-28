# vuemorphic compare

Visual pixel diff between the original React (Claude Design) components and the
converted Vue 3 versions.

## Setup

```bash
# Install Python deps (from the vuemorphic root)
uv pip install pillow numpy playwright
playwright install chromium

# Start the Vue dev server in a separate terminal
cd corpora/claude-design-vue
npm run dev
```

## Run

```bash
# Compare all screens
uv run python compare/compare.py \
  --react-dir "/Users/ceres/Downloads/Flora CAD v2" \
  --vue-url http://localhost:5173

# Compare specific screens
uv run python compare/compare.py --screens v2-main,mf-main-light

# Lower threshold to catch more subtle diffs (default 20 pixels)
uv run python compare/compare.py --threshold 5
```

## Output

```
compare-output/
  summary.json           ← all screens, % changed, region counts
  v2-main/
    react.png            ← full 1440×900 React screenshot
    vue.png              ← full 1440×900 Vue screenshot
    diff.png             ← full diff (red = changed pixels, dim = unchanged)
    region-00/
      react.png          ← cropped region, 3× zoom, labelled
      vue.png
      diff.png
    region-01/
      ...
  mf-main-light/
    ...
```

## Reading the output

- **< 1% changed**: likely just anti-aliasing or font rendering — ignore
- **1–5% changed**: worth looking at the region crops
- **> 5% changed**: structural difference — review carefully

The region crops zoom in 3× on each changed area so subtle differences
(wrong color, wrong font weight, 1px border missing) are visible at a
glance. Show the crops to Claude for targeted fix guidance.

## Adding screens

Edit `compare/screens.json` to add more artboard definitions.
Add the Vue component import to `corpora/claude-design-vue/src/compare-entry.ts`.
