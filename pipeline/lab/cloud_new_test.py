#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip Lab: test the cloud pipeline's new rules on recent headlines.

Runs pipeline/generate.py's checked generation (Haiku writer, keyword filter,
copy rules, Haiku judge, 3 attempts) once per headline, one story at a time.
Needs ANTHROPIC_API_KEY in the environment; the value is never printed.
Output: output/cloud-new/results.json (gitignored).

    python cloud_new_test.py [--days 10]
"""

import argparse
import json
import os
import sys
import time

import config
import generate as cloud

OUT = config.OUTPUT_DIR / "cloud-new"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    a = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set")

    # One fixed story per run: no story fallback, and no dedup list (these
    # headlines are the recent ones the dedup list would exclude).
    cloud.MAX_STORIES = 1
    cloud.fetch_recent_headlines = lambda days=7: []

    entries = sorted(json.loads(config.MAIN_CARTOONS_JSON.read_text(encoding="utf-8"))["cartoons"],
                     key=lambda e: e["date"], reverse=True)[: a.days]
    rows = []
    for e in entries:
        item = {"source": "News", "title": e["headline"], "summary": "", "link": e.get("source_url", "")}
        t0 = time.perf_counter()
        concept, log = cloud.generate_checked_concept(cloud.format_headlines([item]))
        row = {"date": e["date"], "headline": e["headline"], "attempts": len(log),
               "time_s": round(time.perf_counter() - t0, 2), "log": log}
        if concept:
            row.update(punchline=concept["punchline"], speaker=concept.get("speaker", ""),
                       judge=concept.get("judge"), instagram_caption=concept.get("instagram_caption", ""))
        else:
            row["error"] = "failed all attempts: " + " | ".join(x.get("failed", "") for x in log)
        rows.append(row)
        print(f"{e['date']}  {row['attempts']} attempt(s)  {row['time_s']}s  "
              f"{row.get('punchline') or row['error']}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for r in rows if r.get("punchline"))
    print(f"Passed {ok}/{len(rows)}; attempts per story: {[r['attempts'] for r in rows]}")


if __name__ == "__main__":
    main()
