#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip Lab: can local image generation replace Ideogram?

Renders the stored cloud image_prompt for the N most recent cartoons with
each local profile and seed, captions with the published cloud punchline,
and writes a local contact sheet. Output is gitignored and never published.

    python image_compare.py [--days 10] [--profiles sdxl sd15] [--seeds 1001 2002]
"""

import argparse
import html
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import config
import image_local as il
import ollama_client as ollama

OUT = config.OUTPUT_DIR / "image-compare"


class VramSampler:
    """Samples total GPU memory in use via nvidia-smi every 250 ms."""

    def __init__(self, path: Path):
        self.path = path
        self.proc = subprocess.Popen(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", "-lms", "250"],
            stdout=open(path, "w"), stderr=subprocess.DEVNULL)

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)

    def samples(self) -> list[int]:
        return [int(x) for x in self.path.read_text().split() if x.strip().isdigit()]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--profiles", nargs="+", default=["sdxl", "sd15"])
    ap.add_argument("--seeds", nargs="+", type=int, default=[1001, 2002])
    a = ap.parse_args()

    if not il.comfy_up():
        raise SystemExit(f"ComfyUI is not reachable at {config.COMFY_URL}")
    for m in ollama.loaded_models():  # never share the GPU with the text model
        ollama.unload(m)

    entries = sorted(json.loads(config.MAIN_CARTOONS_JSON.read_text(encoding="utf-8"))["cartoons"],
                     key=lambda e: e["date"], reverse=True)[: a.days]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ideogram").mkdir(exist_ok=True)

    il.free_vram()
    time.sleep(2)
    base = VramSampler(OUT / "_vram_baseline.txt")
    time.sleep(3)
    base.stop()
    baseline = max(base.samples() or [0])

    results = {"baseline_vram_mb": baseline, "profiles": {}, "rows": []}
    rows = {e["date"]: {"date": e["date"], "headline": e["headline"], "caption": e.get("punchline", ""),
                        "cloud_prompt": e.get("image_prompt", ""),
                        "clean_prompt": il.build_positive(e.get("image_prompt", "")), "renders": {}}
            for e in entries}

    for e in entries:
        src = config.REPO_ROOT / "website" / e["image"].lstrip("/")
        if src.exists():
            shutil.copy2(src, OUT / "ideogram" / f"{e['date']}.jpg")

    for prof in a.profiles:
        sampler = VramSampler(OUT / f"_vram_{prof}.txt")
        times = []
        for i, e in enumerate(entries):
            for seed in a.seeds:
                name = f"{e['date']}_{prof}_s{seed}.jpg"
                try:
                    r = il.make_cartoon(e.get("image_prompt", ""), e.get("punchline", ""), OUT / name, seed, prof)
                    r["file"] = name
                    r["cold"] = not times  # first render of the profile includes model load
                    times.append(r["render_s"])
                    print(f"{prof:5} {e['date']} seed {seed}: {r['render_s']:6.2f}s  {r['bytes'] // 1024} KB",
                          flush=True)
                except il.ComfyError as ex:
                    r = {"error": str(ex), "seed": seed}
                    print(f"{prof:5} {e['date']} seed {seed}: ERROR {ex}", flush=True)
                rows[e["date"]]["renders"][f"{prof}_s{seed}"] = r
        sampler.stop()
        il.free_vram()
        s = sampler.samples()
        warm = times[1:] or times
        results["profiles"][prof] = {
            "checkpoint": il.PROFILES[prof]["checkpoint"],
            "native_size": il.PROFILES[prof]["size"],
            "steps": il.PROFILES[prof]["steps"],
            "renders": len(times),
            "first_render_s": times[0] if times else None,
            "avg_warm_render_s": round(statistics.mean(warm), 2) if warm else None,
            "avg_all_render_s": round(statistics.mean(times), 2) if times else None,
            "peak_vram_total_mb": max(s) if s else None,
            "peak_vram_over_baseline_mb": (max(s) - baseline) if s else None,
        }
        time.sleep(2)

    results["rows"] = list(rows.values())
    (OUT / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "index.html").write_text(render_sheet(results, a.profiles, a.seeds), encoding="utf-8")
    print(json.dumps(results["profiles"], indent=2))
    print(f"Contact sheet: {OUT / 'index.html'}")


def render_sheet(res: dict, profiles: list[str], seeds: list[int]) -> str:
    e = html.escape
    labels = {"sdxl": "SDXL base 1.0", "sd15": "SD 1.5", "sdxl_lora": "SDXL + LoRA"}
    summary = "".join(
        f"<li><b>{e(labels.get(p, p))}</b>: {v['avg_warm_render_s']} s per image warm "
        f"(first {v['first_render_s']} s with model load), peak VRAM {v['peak_vram_total_mb']} MB total, "
        f"{v['peak_vram_over_baseline_mb']} MB over the desktop baseline, native {v['native_size']} px, "
        f"{v['steps']} steps</li>"
        for p, v in res["profiles"].items())
    rows_html = []
    for r in res["rows"]:
        cells = [
            f'<figure><a href="ideogram/{e(r["date"])}.jpg"><img loading="lazy" src="ideogram/{e(r["date"])}.jpg" '
            f'alt="Ideogram original for {e(r["date"])}"></a><figcaption><b>Ideogram original</b>'
            f'<span>Published image, caption shown as page text on the site</span></figcaption></figure>'
        ]
        for p in profiles:
            for s in seeds:
                x = r["renders"].get(f"{p}_s{s}", {})
                if "file" in x:
                    t = f'{x["render_s"]} s{" (cold, includes model load)" if x.get("cold") else ""}'
                    cells.append(
                        f'<figure><a href="{e(x["file"])}"><img loading="lazy" src="{e(x["file"])}" '
                        f'alt="{e(labels.get(p, p))} seed {s}"></a><figcaption><b>{e(labels.get(p, p))}, '
                        f'seed {s}</b><span>{e(t)}, {x["bytes"] // 1024} KB</span></figcaption></figure>')
                else:
                    cells.append(f'<figure class="err"><figcaption><b>{e(labels.get(p, p))}, seed {s}</b>'
                                 f'<span>{e(x.get("error", "missing"))}</span></figcaption></figure>')
        rows_html.append(f"""
<section class="row">
  <h2>{e(r["date"])}</h2>
  <p class="headline">{e(r["headline"])}</p>
  <p class="caption">Caption: {e(r["caption"]) or "<i>(no punchline)</i>"}</p>
  <div class="grid">{"".join(cells)}</div>
  <details><summary>Prompts</summary>
    <p><b>Cloud prompt (Ideogram):</b> {e(r["cloud_prompt"])}</p>
    <p><b>Local prompt (text instructions removed):</b> {e(r["clean_prompt"])}</p>
    <p><b>Local negative prompt:</b> {e(il.NEGATIVE_PROMPT)}</p>
  </details>
</section>""")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Image comparison</title>
<style>
:root {{ --bg:#faf9f7; --fg:#1d1d1b; --muted:#6b6a66; --card:#fff; --line:#e4e1db; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161614; --fg:#ecebe7; --muted:#a3a19b; --card:#22221f; --line:#34332f; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 Georgia, serif; }}
main {{ max-width:1400px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:1.6rem; margin:0 0 .25rem; }}
h2 {{ font-size:1.1rem; margin:0; }}
.lede, .headline, figcaption span, details {{ color:var(--muted); }}
.summary {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 16px 12px 32px; }}
.row {{ border-top:1px solid var(--line); padding-top:20px; margin-top:24px; }}
.headline {{ margin:.2rem 0; }}
.caption {{ margin:.2rem 0 .8rem; font-weight:bold; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 220px), 1fr)); gap:12px; }}
figure {{ margin:0; background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
figure img {{ display:block; width:100%; height:auto; aspect-ratio:1; object-fit:cover; background:#fff; }}
figcaption {{ padding:8px 10px; font-size:.85rem; }}
figcaption b, figcaption span {{ display:block; }}
figure.err {{ display:flex; align-items:center; min-height:120px; }}
details {{ margin-top:10px; font-size:.85rem; overflow-wrap:anywhere; }}
summary {{ cursor:pointer; }}
</style></head>
<body><main>
<h1>Image comparison</h1>
<p class="lede">Can local image generation replace Ideogram? Same cloud prompt, text instructions removed,
same published punchline set below the panel. Local only, not published. {len(res["rows"])} dates, 25 steps.</p>
<ul class="summary">{summary}</ul>
{"".join(rows_html)}
</main></body></html>
"""


if __name__ == "__main__":
    main()
