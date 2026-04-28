"""
Visual comparison: React (Claude Design source) vs Vue (converted) side-by-side.

For each screen in screens.json:
  - Screenshots the React harness (static file server)
  - Screenshots the Vue harness (Vite dev server)
  - Pixel-diffs them with Pillow
  - Finds changed regions (connected components of diff pixels)
  - Outputs: react.png, vue.png, diff.png, and per-region crops
    (full size + 3x zoom) for every significant diff region

Output directory: compare-output/{screen-id}/

Usage:
  uv run python compare/compare.py [--screens v2-main,mf-main-light] [--threshold 10]
                                   [--react-dir /path/to/flora-cad-v2]
                                   [--vue-url http://localhost:5173]
                                   [--out compare-output]
"""
from __future__ import annotations

import argparse
import http.server
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import NamedTuple

# ── Dependencies ───────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageChops, ImageFilter
    import numpy as np
except ImportError:
    print("Missing deps. Run: uv pip install pillow numpy playwright")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Missing playwright. Run: uv pip install playwright && playwright install chromium")
    sys.exit(1)


# ── Types ──────────────────────────────────────────────────────────────────────

class Screen(NamedTuple):
    id: str
    label: str
    component: str
    props: dict
    w: int
    h: int


class Region(NamedTuple):
    x: int
    y: int
    w: int
    h: int
    pixel_count: int


# ── Static file server for React harness ──────────────────────────────────────

def start_static_server(directory: Path, port: int = 7331) -> threading.Thread:
    """Serve a directory over HTTP on the given port."""
    directory = directory.resolve()

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)
        def log_message(self, *_): pass

    server = http.server.HTTPServer(("127.0.0.1", port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  React static server: http://127.0.0.1:{port}/  (serving {directory})")
    return thread


def wait_for_url(url: str, timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Server never responded: {url}")


# ── Pixel diff ─────────────────────────────────────────────────────────────────

def diff_images(img_a: Image.Image, img_b: Image.Image) -> tuple[Image.Image, np.ndarray]:
    """Return (diff_image, diff_mask_array).

    diff_image: RGBA where changed pixels are red, unchanged are semi-transparent.
    diff_mask_array: boolean numpy array, True = changed pixel.
    """
    a = np.array(img_a.convert("RGB"), dtype=np.int32)
    b = np.array(img_b.convert("RGB"), dtype=np.int32)

    delta = np.abs(a - b).sum(axis=2)       # per-pixel colour distance (0–765)
    changed = delta > 15                     # threshold: small anti-alias noise ignored

    diff = Image.new("RGBA", img_a.size, (0, 0, 0, 0))
    draw_a = np.array(img_a.convert("RGBA"))

    # Unchanged pixels: original at 25% opacity
    draw_a[~changed, 3] = 40
    # Changed pixels: vivid red at full opacity
    draw_a[changed] = [220, 40, 40, 255]

    return Image.fromarray(draw_a), changed


# ── Connected-component region finding ────────────────────────────────────────

def find_diff_regions(
    mask: np.ndarray,
    min_pixels: int = 20,
    padding: int = 24,
) -> list[Region]:
    """Find bounding boxes of connected diff regions.

    Uses a simple flood-fill grouping — no scipy required.
    Returns regions sorted by pixel_count descending.
    """
    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    regions: list[Region] = []

    for start_y in range(h):
        for start_x in range(w):
            if not mask[start_y, start_x] or visited[start_y, start_x]:
                continue

            # BFS flood fill
            queue = [(start_x, start_y)]
            visited[start_y, start_x] = True
            xs, ys = [start_x], [start_y]
            count = 1

            while queue:
                cx, cy = queue.pop()
                for nx, ny in [(cx-1,cy),(cx+1,cy),(cx,cy-1),(cx,cy+1),
                               (cx-1,cy-1),(cx+1,cy-1),(cx-1,cy+1),(cx+1,cy+1)]:
                    if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((nx, ny))
                        xs.append(nx)
                        ys.append(ny)
                        count += 1

            if count < min_pixels:
                continue

            # Bounding box + padding
            rx = max(0, min(xs) - padding)
            ry = max(0, min(ys) - padding)
            rw = min(w, max(xs) + padding + 1) - rx
            rh = min(h, max(ys) + padding + 1) - ry
            regions.append(Region(rx, ry, rw, rh, count))

    # Merge overlapping regions
    regions = _merge_overlapping(regions)
    return sorted(regions, key=lambda r: r.pixel_count, reverse=True)


def _merge_overlapping(regions: list[Region]) -> list[Region]:
    """Merge any two regions whose bounding boxes overlap."""
    changed = True
    while changed:
        changed = False
        merged: list[Region] = []
        used = [False] * len(regions)
        for i, a in enumerate(regions):
            if used[i]:
                continue
            x1, y1, x2, y2 = a.x, a.y, a.x + a.w, a.y + a.h
            pc = a.pixel_count
            for j, b in enumerate(regions):
                if j <= i or used[j]:
                    continue
                bx1, by1, bx2, by2 = b.x, b.y, b.x + b.w, b.y + b.h
                if x1 < bx2 and x2 > bx1 and y1 < by2 and y2 > by1:
                    x1, y1 = min(x1, bx1), min(y1, by1)
                    x2, y2 = max(x2, bx2), max(y2, by2)
                    pc += b.pixel_count
                    used[j] = True
                    changed = True
            merged.append(Region(x1, y1, x2-x1, y2-y1, pc))
            used[i] = True
        regions = merged
    return regions


# ── Screenshot helpers ─────────────────────────────────────────────────────────

def screenshot_react(page, react_base: str, screen: Screen) -> Image.Image:
    props_json = json.dumps(screen.props)
    url = f"{react_base}/react-harness.html?component={screen.component}&props={props_json}&w={screen.w}&h={screen.h}"
    page.goto(url)
    page.wait_for_selector("[data-ready='true']", timeout=20000)
    page.set_viewport_size({"width": screen.w, "height": screen.h})
    raw = page.screenshot(type="png", clip={"x": 0, "y": 0, "width": screen.w, "height": screen.h})
    from io import BytesIO
    return Image.open(BytesIO(raw))


def screenshot_vue(page, vue_base: str, screen: Screen) -> Image.Image:
    props_json = json.dumps(screen.props)
    url = f"{vue_base}/compare.html?component={screen.component}&props={props_json}&w={screen.w}&h={screen.h}"
    page.goto(url)
    page.wait_for_selector("[data-ready='true']", timeout=20000)
    page.set_viewport_size({"width": screen.w, "height": screen.h})
    raw = page.screenshot(type="png", clip={"x": 0, "y": 0, "width": screen.w, "height": screen.h})
    from io import BytesIO
    return Image.open(BytesIO(raw))


# ── Crop + zoom helpers ────────────────────────────────────────────────────────

ZOOM = 3
LABEL_H = 28

def _labeled(img: Image.Image, text: str, color: tuple) -> Image.Image:
    out = Image.new("RGB", (img.width, img.height + LABEL_H), color)
    draw = ImageDraw.Draw(out)
    draw.rectangle([0, 0, img.width, LABEL_H], fill=color)
    draw.text((6, 6), text, fill=(255, 255, 255))
    out.paste(img, (0, LABEL_H))
    return out


def save_region_crops(
    react: Image.Image,
    vue: Image.Image,
    diff: Image.Image,
    region: Region,
    out_dir: Path,
    idx: int,
) -> None:
    box = (region.x, region.y, region.x + region.w, region.y + region.h)
    r_dir = out_dir / f"region-{idx:02d}"
    r_dir.mkdir(parents=True, exist_ok=True)

    for img, name, color in [
        (react, "react", (30, 100, 200)),
        (vue,   "vue",   (60, 160, 80)),
        (diff,  "diff",  (180, 40, 40)),
    ]:
        crop = img.crop(box)
        zoomed = crop.resize((crop.width * ZOOM, crop.height * ZOOM), Image.NEAREST)
        labeled = _labeled(zoomed, f"{name}  [{region.x},{region.y} {region.w}×{region.h}]  {region.pixel_count}px changed", color)
        labeled.save(r_dir / f"{name}.png")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="React vs Vue pixel comparison")
    parser.add_argument("--screens",    help="Comma-separated screen IDs to compare (default: all)")
    parser.add_argument("--react-dir",  default="/Users/ceres/Downloads/Flora CAD v2",
                        help="Path to the Claude Design download directory")
    parser.add_argument("--vue-url",    default="http://localhost:5173",
                        help="Vite dev server URL for the Vue project")
    parser.add_argument("--out",        default="compare-output",
                        help="Output directory")
    parser.add_argument("--threshold",  type=int, default=20,
                        help="Min changed pixels to report a region")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    screens_json = script_dir / "screens.json"
    all_screens = [Screen(**s) for s in json.loads(screens_json.read_text())]

    if args.screens:
        wanted = set(args.screens.split(","))
        screens = [s for s in all_screens if s.id in wanted]
    else:
        screens = all_screens

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    react_dir = Path(args.react_dir)
    harness_src = script_dir / "react-harness.html"
    import shutil
    shutil.copy(harness_src, react_dir / "react-harness.html")

    # Start React static server
    react_port = 7331
    start_static_server(react_dir, react_port)
    react_base = f"http://127.0.0.1:{react_port}"
    wait_for_url(f"{react_base}/react-harness.html")

    # Wait for Vue dev server (check compare.html specifically)
    vue_check_url = args.vue_url.rstrip("/") + "/compare.html"
    print(f"  Waiting for Vue dev server at {vue_check_url}...")
    wait_for_url(vue_check_url)

    summary = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        r_page = browser.new_page()
        v_page = browser.new_page()

        for screen in screens:
            print(f"\n▶  {screen.id}  ({screen.component}  {screen.w}×{screen.h})")
            out_dir = out_root / screen.id
            out_dir.mkdir(parents=True, exist_ok=True)

            print(f"   Screenshotting React…")
            react_img = screenshot_react(r_page, react_base, screen)
            react_img.save(out_dir / "react.png")

            print(f"   Screenshotting Vue…")
            try:
                vue_img = screenshot_vue(v_page, args.vue_url, screen)
                vue_img.save(out_dir / "vue.png")
            except Exception as exc:
                print(f"   ✗ Vue screenshot failed: {exc}")
                continue

            # Ensure same size (component may render slightly differently)
            if react_img.size != vue_img.size:
                vue_img = vue_img.resize(react_img.size, Image.LANCZOS)

            print(f"   Diffing…")
            diff_img, diff_mask = diff_images(react_img, vue_img)
            diff_img.save(out_dir / "diff.png")

            changed_px = int(diff_mask.sum())
            total_px = diff_mask.size
            pct = changed_px / total_px * 100

            regions = find_diff_regions(diff_mask, min_pixels=args.threshold)
            print(f"   {changed_px:,} changed pixels ({pct:.1f}%)  →  {len(regions)} region(s)")

            for i, region in enumerate(regions[:20]):  # cap at 20 regions per screen
                save_region_crops(react_img, vue_img, diff_img, region, out_dir, i)
                print(f"     region-{i:02d}: [{region.x},{region.y}] {region.w}×{region.h}  ({region.pixel_count}px)")

            summary.append({
                "id": screen.id,
                "label": screen.label,
                "component": screen.component,
                "changed_px": changed_px,
                "total_px": total_px,
                "pct_changed": round(pct, 2),
                "regions": len(regions),
            })

        browser.close()

    # Write summary
    summary_path = out_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\n✓ Done. Summary: {summary_path}")
    for s in summary:
        mark = "✓" if s["pct_changed"] < 1 else "⚠" if s["pct_changed"] < 5 else "✗"
        print(f"  {mark} {s['id']:20s}  {s['pct_changed']:5.1f}%  ({s['regions']} regions)")


if __name__ == "__main__":
    main()
