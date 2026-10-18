#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip Lab: count CLIP tokens exactly as ComfyUI's SDXL encoder sees them.

Runs under ComfyUI's embedded Python (it imports ComfyUI's tokenizer), not the
lab venv. Reads output/image-compare/prompts.json from `image_compare.py prompts`
and writes output/image-compare/tokens.json.

    C:\\AI\\ComfyUI\\python_embeded\\python.exe clip_tokens.py
"""

import json
import os
import sys
from pathlib import Path

COMFY_ROOT = Path(os.environ.get("NS_COMFY_DIR", r"C:\AI\ComfyUI")) / "ComfyUI"
sys.path.insert(0, str(COMFY_ROOT))
from comfy.sdxl_clip import SDXLTokenizer  # noqa: E402

OUT = Path(__file__).resolve().parent / "output" / "image-compare"
USABLE = 75  # 77 minus start and end tokens


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads((OUT / "prompts.json").read_text(encoding="utf-8"))
    tok = SDXLTokenizer()
    raw = tok.clip_l.tokenizer  # the underlying CLIP BPE tokenizer

    def count(text: str) -> int:
        return len(raw(text)["input_ids"]) - 2

    def chunks(text: str) -> int:
        return len(tok.tokenize_with_weights(text)["l"])

    res = {"style_prefixes": {k: count(v) for k, v in data["style_prefixes"].items()}, "prompts": {}}
    multi = 0
    for key, p in data["prompts"].items():
        c = chunks(p["positive"])
        multi += c > 1
        res["prompts"][key] = {"positive_tokens": count(p["positive"]), "positive_chunks": c,
                               "negative_tokens": count(p["negative"]), "negative_chunks": chunks(p["negative"])}
    res["multi_chunk"] = f"{multi} of {len(data['prompts'])}"
    (OUT / "tokens.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

    print(f"Style prefix: locked {res['style_prefixes']['locked']} tokens, ink {res['style_prefixes']['ink']} tokens")
    for v in ("original", "compressed", "ink"):
        rows = [x for k, x in res["prompts"].items() if k.endswith("|" + v)]
        if rows:
            t = [x["positive_tokens"] for x in rows]
            print(f"{v:10} positive tokens min {min(t)} max {max(t)} avg {sum(t) / len(t):.0f}; "
                  f"over one chunk: {sum(x['positive_chunks'] > 1 for x in rows)} of {len(rows)}; "
                  f"negative {rows[0]['negative_tokens']} tokens / {rows[0]['negative_chunks']} chunk(s)")


if __name__ == "__main__":
    main()
