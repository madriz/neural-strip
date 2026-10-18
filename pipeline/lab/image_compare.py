#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip Lab: cloud vs local comparison sheet (local only, never published).

Steps, run in order; each writes into output/image-compare/ (gitignored):

    python image_compare.py compress                 # qwen3:8b rewrites each scene, key gag first
    python image_compare.py render --variant original
    python image_compare.py render --variant compressed
    python image_compare.py render --variant ink     # short ink prefix + compressed scene, levels
    python image_compare.py prompts                  # export prompts for clip_tokens.py
    python image_compare.py sheet [--open]           # caption panels, write index.html

Text columns are read if present:
    output/text-compare/<tag>/results.json   (local one-liners, tags v1, v2, ...)
    output/cloud-new/results.json            (cloud pipeline with the new rules)
"""

import argparse
import html
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

import config
import generate as cloud
import image_local as il
import ollama_client as ollama

OUT = config.OUTPUT_DIR / "image-compare"
PANELS = OUT / "panels"
CAPTIONED = OUT / "captioned"
TEXT_DIR = config.OUTPUT_DIR / "text-compare"
CLOUD_NEW = config.OUTPUT_DIR / "cloud-new" / "results.json"
DAYS = 10
SEEDS = (1001, 2002)


def recent_entries(days: int = DAYS) -> list[dict]:
    data = json.loads(config.MAIN_CARTOONS_JSON.read_text(encoding="utf-8"))["cartoons"]
    return sorted(data, key=lambda e: e["date"], reverse=True)[:days]


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Step 1: prompt compression ───────────────────────────────────────────────

COMPRESS_SYSTEM = """\
You turn a cartoon scene description into a short prompt for an image model.
Rules:
- Start with the key visual gag: the main subject, what they are doing, and the ONE prop that
  makes the joke. Then at most one short clause of setting.
- Under 40 words. Plain visual language.
- No text in the image: never ask for words, labels, signs with writing, screens with text, logos
  or captions. If the joke depends on a label, show it visually instead (a line on a chart
  dropping, a towering stack of paper, a red X).
- No names of real people, companies, products or brands. Use generic roles and objects.
- No style words (line art, colors, cartoon, illustration). Style is added separately.
Return JSON {"prompt": "..."}."""

COMPRESS_SCHEMA = {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}
MAX_PROMPT_WORDS = 40


def cmd_compress(_a) -> None:
    out = {}
    try:
        for e in recent_entries():
            scene = il.clean_scene(e.get("image_prompt", ""))
            note, t_total = "", 0.0
            for attempt in range(1, 4):
                res, st = ollama.chat_json(config.TEXT_MODEL, COMPRESS_SYSTEM,
                                           f"Scene:\n{scene}{note}", COMPRESS_SCHEMA,
                                           temperature=0.3, think=True, num_ctx=8192)
                t_total += st["seconds"]
                p = re.sub(r"\s+", " ", res.get("prompt", "")).strip()
                words = len(p.split())
                problem = ("too long" if words >= MAX_PROMPT_WORDS else
                           "contains quoted text" if re.search(r"[\"“”]|'[^']+'", p) else "")
                if not problem:
                    break
                note = f"\n\nYour last prompt was rejected ({problem}, {words} words). Try again."
            out[e["date"]] = {"prompt": p, "words": words, "seconds": round(t_total, 2),
                              "attempts": attempt, "problem": problem}
            print(f"{e['date']}  {words:2} words  {t_total:5.1f}s  {p}", flush=True)
    finally:
        ollama.unload(config.TEXT_MODEL)
    save_json(OUT / "compressed.json", out)


# ── Step 2: panel renders ────────────────────────────────────────────────────

class VramSampler:
    """Samples total GPU memory in use via nvidia-smi every 250 ms."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", "-lms", "250"],
            stdout=open(path, "w"), stderr=subprocess.DEVNULL)

    def stop(self) -> list[int]:
        self.proc.terminate()
        self.proc.wait(timeout=10)
        return [int(x) for x in self.path.read_text().split() if x.strip().isdigit()]


VARIANTS = ("original", "compressed", "ink")
VARIANT_LABELS = {"original": "SDXL, cloud prompt", "compressed": "SDXL, compressed prompt",
                  "ink": "SDXL ink"}


def variant_prompts(variant: str, e: dict, compressed: dict) -> tuple[str, str]:
    """(positive, negative) for one date."""
    if variant == "original":
        return il.build_positive(e.get("image_prompt", "")), il.NEGATIVE_PROMPT
    scene = compressed[e["date"]]["prompt"]
    if variant == "compressed":
        return il.build_positive(scene, gag_first=True), il.NEGATIVE_PROMPT
    return il.build_ink_positive(scene), il.INK_NEGATIVE


def cmd_prompts(_a) -> None:
    """Export every positive and negative prompt, plus the two style prefixes, for token counting."""
    compressed = load_json(OUT / "compressed.json", {})
    out = {"style_prefixes": {"locked": cloud.IMAGE_PROMPT_PREFIX.strip(), "ink": il.INK_PREFIX},
           "prompts": {}}
    for e in recent_entries():
        for v in VARIANTS:
            if v != "original" and e["date"] not in compressed:
                continue
            pos, neg = variant_prompts(v, e, compressed)
            out["prompts"][f"{e['date']}|{v}"] = {"positive": pos, "negative": neg}
    save_json(OUT / "prompts.json", out)
    print(f"Wrote {len(out['prompts'])} prompt pairs to {OUT / 'prompts.json'}")


def cmd_render(a) -> None:
    if not il.comfy_up():
        raise SystemExit(f"ComfyUI is not reachable at {config.COMFY_URL}")
    for m in ollama.loaded_models():  # never share the GPU with the text model
        ollama.unload(m)
    compressed = load_json(OUT / "compressed.json", {})
    if a.variant != "original" and not compressed:
        raise SystemExit("Run the compress step first")

    il.free_vram()
    time.sleep(2)
    s0 = VramSampler(OUT / "_vram_base.txt")
    time.sleep(3)
    baseline = max(s0.stop() or [0])

    renders = load_json(OUT / "renders.json", {})
    renders[a.variant] = {"items": {}}
    sampler = VramSampler(OUT / f"_vram_{a.variant}.txt")
    times = []
    for e in recent_entries():
        positive, negative = variant_prompts(a.variant, e, compressed)
        post = il.ink_levels if a.variant == "ink" else None
        for seed in SEEDS:
            key = f"{e['date']}_s{seed}"
            try:
                r = il.make_panel(positive, PANELS / f"{e['date']}_{a.variant}_s{seed}.png", seed, "sdxl",
                                  negative=negative, post=post)
                r["cold"] = not times
                times.append(r["render_s"])
                print(f"{a.variant:10} {key}: {r['render_s']:6.2f}s", flush=True)
            except il.ComfyError as ex:
                r = {"error": str(ex), "seed": seed}
                print(f"{a.variant:10} {key}: ERROR {ex}", flush=True)
            renders[a.variant]["items"][key] = r
    samples = sampler.stop()
    il.free_vram()
    warm = times[1:] or times
    renders[a.variant]["summary"] = {
        "renders": len(times), "first_render_s": times[0] if times else None,
        "avg_warm_render_s": round(statistics.mean(warm), 2) if warm else None,
        "peak_vram_total_mb": max(samples) if samples else None,
        "baseline_vram_mb": baseline,
    }
    save_json(OUT / "renders.json", renders)
    print(json.dumps(renders[a.variant]["summary"], indent=2))


# ── Step 3: captions and sheet ───────────────────────────────────────────────

def caption_file(src: Path, caption: str, name: str) -> str:
    """Compose src (Ideogram jpg or local panel png) with a caption. Returns relative path."""
    dst = CAPTIONED / name
    if caption:
        il.save_jpeg(il.compose(Image.open(src).convert("RGB"), caption), dst)
    else:
        il.save_jpeg(Image.open(src).convert("RGB").resize((il.CANVAS, il.CANVAS)), dst)
    return dst.relative_to(OUT).as_posix()


def text_runs() -> dict[str, dict]:
    """Local one-liner runs by tag: {tag: {date: row}}."""
    runs = {}
    if TEXT_DIR.exists():
        for d in sorted(p for p in TEXT_DIR.iterdir() if p.is_dir()):
            rows = load_json(d / "results.json", [])
            if rows:
                runs[d.name] = {r["date"]: r for r in rows}
    return runs


def cmd_sheet(a) -> None:
    renders = load_json(OUT / "renders.json", {})
    compressed = load_json(OUT / "compressed.json", {})
    local = text_runs()
    cloud_new = {r["date"]: r for r in load_json(CLOUD_NEW, [])}
    tokens = load_json(OUT / "tokens.json", {})
    e_ = html.escape

    def cell(img, title, sub=""):
        return (f'<figure><a href="{e_(img)}"><img loading="lazy" src="{e_(img)}" alt="{e_(title)}"></a>'
                f'<figcaption><b>{e_(title)}</b>{f"<span>{e_(sub)}</span>" if sub else ""}</figcaption></figure>')

    def missing(title, why):
        return f'<figure class="missing"><figcaption><b>{e_(title)}</b><span>{e_(why)}</span></figcaption></figure>'

    def tsec(variant, date, seed):
        x = renders.get(variant, {}).get("items", {}).get(f"{date}_s{seed}", {})
        if "render_s" not in x:
            return None
        return f'{x["render_s"]} s' + (" (cold, includes model load)" if x.get("cold") else "")

    rows_html = []
    for e in recent_entries():
        d, cloud_cap = e["date"], e.get("punchline", "")
        cells = []
        ideo = config.REPO_ROOT / "website" / e["image"].lstrip("/")
        cells.append(cell(caption_file(ideo, cloud_cap, f"{d}_ideogram_cloud.jpg"),
                          "Ideogram, cloud caption", "Published image"))
        for variant in VARIANTS:
            label = VARIANT_LABELS[variant]
            if variant == "ink" and "ink" not in renders:
                continue
            for seed in SEEDS:
                panel = PANELS / f"{d}_{variant}_s{seed}.png"
                title = f"{label}, seed {seed}, cloud caption"
                if panel.exists():
                    cells.append(cell(caption_file(panel, cloud_cap, f"{d}_{variant}_s{seed}_cloud.jpg"),
                                      title, tsec(variant, d, seed) or ""))
                else:
                    cells.append(missing(title, "not rendered yet"))
        seed1 = PANELS / f"{d}_original_s{SEEDS[0]}.png"
        for tag, rows in local.items():
            r = rows.get(d)
            title = f"SDXL seed {SEEDS[0]}, local one-liner {tag}"
            if r and seed1.exists() and not str(r.get("local", "")).startswith("FAILED"):
                cells.append(cell(caption_file(seed1, r["local"], f"{d}_original_s{SEEDS[0]}_local_{tag}.jpg"),
                                  title, f'{r.get("time_s")} s to write'))
            else:
                cells.append(missing(title, "failed" if r else "not run yet"))
        if cloud_new:
            r = cloud_new.get(d)
            title = f"SDXL seed {SEEDS[0]}, cloud one-liner (new rules)"
            if r and r.get("punchline") and seed1.exists():
                cells.append(cell(caption_file(seed1, r["punchline"], f"{d}_original_s{SEEDS[0]}_cloudnew.jpg"),
                                  title, f'{r.get("attempts", "?")} attempts'))
            else:
                cells.append(missing(title, (r or {}).get("error", "not run yet")))

        # Text block
        cloud_joke = " / ".join(x for x in (e.get("setup", ""), e.get("punchline", "")) if x)
        lines = [f'<dt>Headline</dt><dd>{e_(e["headline"])}</dd>',
                 f'<dt>Cloud (published)</dt><dd>{e_(cloud_joke)}</dd>']
        for tag, rows in local.items():
            r = rows.get(d)
            if not r:
                continue
            verdict = r.get("judge_verdict", "pass" if not str(r.get("local", "")).startswith("FAILED") else "fail")
            lines.append(f'<dt>Local one-liner {e_(tag)}</dt><dd>{e_(r["local"])}'
                         f'<span class="meta">Judge: {e_(verdict)}. {e_(str(r.get("judge", "")))} '
                         f'{e_(str(r.get("time_s")))} s, {r.get("attempts", "?")} attempt(s).</span>'
                         f'{ranking_html(r)}</dd>')
        if cloud_new.get(d):
            r = cloud_new[d]
            lines.append(f'<dt>Cloud one-liner (new rules)</dt><dd>{e_(r.get("punchline") or r.get("error", ""))}'
                         f'<span class="meta">Judge: {e_(str((r.get("judge") or {}).get("verdict", "")))}. '
                         f'{e_(str((r.get("judge") or {}).get("reason", "")))} '
                         f'{r.get("attempts", "?")} attempt(s).</span></dd>')
        comp = compressed.get(d, {})

        def tok(v):
            t = tokens.get("prompts", {}).get(f"{d}|{v}")
            return f" [{t['positive_tokens']} CLIP tokens, {t['positive_chunks']} chunk(s)]" if t else ""

        prompts = (f'<p><b>Cloud prompt (Ideogram):</b> {e_(e.get("image_prompt", ""))}</p>'
                   f'<p><b>SDXL, cloud prompt, text removed{tok("original")}:</b> '
                   f'{e_(il.build_positive(e.get("image_prompt", "")))}</p>')
        if comp:
            prompts += (f'<p><b>SDXL, compressed ({comp["words"]} words, {comp["seconds"]} s to write)'
                        f'{tok("compressed")}:</b> {e_(il.build_positive(comp["prompt"], gag_first=True))}</p>'
                        f'<p><b>SDXL ink{tok("ink")}:</b> {e_(il.build_ink_positive(comp["prompt"]))}</p>')
        rows_html.append(f"""
<section class="row">
  <h2>{e_(d)}</h2>
  <div class="grid">{"".join(cells)}</div>
  <dl>{"".join(lines)}</dl>
  <details><summary>Prompts</summary>{prompts}<p><b>Negative:</b> {e_(il.NEGATIVE_PROMPT)}</p></details>
</section>""")

    summary = []
    if tokens.get("style_prefixes"):
        sp = tokens["style_prefixes"]
        summary.append(f"<li><b>Style prefix length</b>: locked {sp['locked']} CLIP tokens, ink {sp['ink']}. "
                       f"CLIP reads 75 tokens per chunk; ComfyUI splits longer prompts into chunks, "
                       f"it does not truncate. Prompts over one chunk: {tokens.get('multi_chunk', '?')}.</li>")
    for variant in VARIANTS:
        label = VARIANT_LABELS[variant]
        s = renders.get(variant, {}).get("summary")
        if s:
            summary.append(f"<li><b>{label}</b>: {s['avg_warm_render_s']} s per image warm "
                           f"(first {s['first_render_s']} s), peak VRAM {s['peak_vram_total_mb']} MB total "
                           f"(desktop baseline {s['baseline_vram_mb']} MB)</li>")
    for tag, rows in local.items():
        t = [r["time_s"] for r in rows.values() if r.get("time_s")]
        ok = sum(1 for r in rows.values() if not str(r.get("local", "")).startswith("FAILED"))
        summary.append(f"<li><b>Local one-liner {e_(tag)}</b>: {ok} of {len(rows)} passed, "
                       f"{round(statistics.mean(t), 1) if t else '?'} s average to write</li>")

    page = PAGE.format(summary="".join(summary), rows="".join(rows_html), n=len(rows_html))
    (OUT / "index.html").write_text(page, encoding="utf-8")
    print(f"Contact sheet: {OUT / 'index.html'}")
    if a.open:
        os.startfile(str(OUT / "index.html"))  # default browser


def ranking_html(r: dict) -> str:
    """The best-of-4 log for the passing attempt: drafts in rank order, which one was judged."""
    attempts = r.get("all_drafts") or []
    last = next((x for x in reversed(attempts) if x.get("stage") == "passed"), None)
    if not last or not last.get("ranking"):
        return ""
    e_ = html.escape
    rejected = [x for x in last.get("drafts", []) if x.get("rejected")]
    items = "".join(f"<li>{e_(p)}</li>" for p in last["ranking"]["order"])
    rej = "".join(f"<li>{e_(x['punchline'])} <i>({e_(x['rejected'])})</i></li>" for x in rejected)
    judged = "; ".join(f"#{j.get('rank')}: {j.get('verdict', 'error')}" for j in last.get("judged", []))
    return (f'<details class="rank"><summary>Ranking (chosen: #{last.get("chosen_rank")}; judged {e_(judged)})'
            f'</summary><ol>{items}</ol><p>Ranker: {e_(last["ranking"]["reason"])}</p>'
            f'{f"<p>Dropped before ranking:</p><ul>{rej}</ul>" if rej else ""}</details>')


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cloud vs local</title>
<style>
:root {{ --bg:#faf9f7; --fg:#1d1d1b; --muted:#6b6a66; --card:#fff; --line:#e4e1db; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161614; --fg:#ecebe7; --muted:#a3a19b; --card:#22221f; --line:#34332f; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 Georgia, serif; }}
main {{ max-width:1500px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:1.6rem; margin:0 0 .25rem; }}
h2 {{ font-size:1.15rem; margin:0 0 .6rem; }}
.lede, figcaption span, details, .meta {{ color:var(--muted); }}
.summary {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 16px 12px 32px; }}
.row {{ border-top:1px solid var(--line); padding-top:20px; margin-top:24px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 200px), 1fr)); gap:12px; }}
figure {{ margin:0; background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
figure img {{ display:block; width:100%; height:auto; aspect-ratio:1; background:#fff; }}
figcaption {{ padding:8px 10px; font-size:.82rem; }}
figcaption b, figcaption span {{ display:block; }}
figure.missing {{ display:flex; align-items:center; min-height:120px; border-style:dashed; }}
dl {{ margin:14px 0 0; display:grid; grid-template-columns:minmax(0, 11rem) minmax(0, 1fr); gap:6px 14px; }}
dt {{ font-weight:bold; }}
dd {{ margin:0; overflow-wrap:anywhere; }}
.meta {{ display:block; font-size:.82rem; }}
@media (max-width:560px) {{ dl {{ grid-template-columns:minmax(0, 1fr); }} dd {{ margin-bottom:6px; }} }}
details {{ margin-top:8px; font-size:.85rem; overflow-wrap:anywhere; }}
summary {{ cursor:pointer; }}
</style></head>
<body><main>
<h1>Cloud vs local</h1>
<p class="lede">Last {n} cloud cartoons. Every image column uses the same caption treatment below the panel.
Local only, not published.</p>
<ul class="summary">{summary}</ul>
{rows}
</main></body></html>
"""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("compress")
    sub.add_parser("prompts")
    r = sub.add_parser("render")
    r.add_argument("--variant", choices=list(VARIANTS), required=True)
    s = sub.add_parser("sheet")
    s.add_argument("--open", action="store_true", help="open the sheet in the default browser")
    a = ap.parse_args()
    {"compress": cmd_compress, "prompts": cmd_prompts, "render": cmd_render, "sheet": cmd_sheet}[a.cmd](a)


if __name__ == "__main__":
    main()
