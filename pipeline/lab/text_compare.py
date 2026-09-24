#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip Lab: local one-liners vs published cloud punchlines.

For the N most recent cloud cartoons, writes a local one-liner for the same
headline and prints a markdown table. Output is gitignored.

    python text_compare.py [--days 10]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import config
import generate_local as gl
import ollama_client as ollama

OUT = config.OUTPUT_DIR / "text-compare"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    entries = sorted(json.loads(config.MAIN_CARTOONS_JSON.read_text(encoding="utf-8"))["cartoons"],
                     key=lambda e: e["date"], reverse=True)[: a.days]
    rows = []
    for e in entries:
        label = datetime.strptime(e["date"], "%Y-%m-%d").strftime("%B %d, %Y")
        story = {"title": e["headline"], "link": e.get("source_url", "")}
        try:
            c = gl.run_for_story(story, date_label=label, unload_after=False)
            L = c["lab"]
            row = {"date": e["date"], "headline": e["headline"],
                   "cloud": " / ".join(x for x in (e.get("setup", ""), e.get("punchline", "")) if x),
                   "local": c["punchline"], "caption": c["instagram_caption"],
                   "time_s": L["timings_s"]["total"], "attempts": L["attempts_used"],
                   "chosen_rank": L["attempts"][-1].get("chosen_rank"),
                   "rank_reason": L["rank_reason"], "judge": L["judge"]["reason"],
                   "all_drafts": L["attempts"]}
        except gl.LabFailure as ex:
            row = {"date": e["date"], "headline": e["headline"],
                   "cloud": " / ".join(x for x in (e.get("setup", ""), e.get("punchline", "")) if x),
                   "local": f"FAILED: {ex}", "time_s": None, "attempts": 3, "all_drafts": ex.attempts}
        rows.append(row)
        print(f"{row['date']}  {row['time_s']}s  {row['local']}", flush=True)

    gl._unload_all(False)
    (OUT / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    md = ["| Date | Cloud punchline (published) | Local one-liner | Time |", "|---|---|---|---|"]
    for r in rows:
        t = f"{r['time_s']} s" + (f", {r['attempts']} attempts" if r["attempts"] > 1 else "") \
            if r["time_s"] is not None else "failed"
        md.append(f"| {r['date']} | {r['cloud'].replace('|', '/')} | {r['local'].replace('|', '/')} | {t} |")
    (OUT / "table.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"VRAM models after: {ollama.loaded_models()}")


if __name__ == "__main__":
    main()
