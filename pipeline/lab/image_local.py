#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip Lab: local cartoon image generation through ComfyUI.

Drives ComfyUI's HTTP API with the saved workflow in
workflows/txt2img_api.json, then sets the caption below the panel with
Pillow. Final output: 1024x1024 JPEG, about 200 KB, like the main site.

Model profiles are swappable (checkpoint and optional LoRA):
    NS_IMAGE_PROFILE=sdxl | sd15 | sdxl_lora
    NS_IMAGE_LORA=<file in ComfyUI/models/loras>   NS_IMAGE_LORA_STRENGTH=0.8

Usage:
    python image_local.py --scene "..." --caption "..." --out test.jpg [--profile sd15] [--seed 7]
"""

import argparse
import copy
import io
import json
import os
import re
import time
import uuid
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

import config  # noqa: F401  (puts pipeline/ on sys.path)
from config import COMFY_URL, WORKFLOWS_DIR
import generate as cloud  # locked style prefix, read-only

# ── Model profiles ───────────────────────────────────────────────────────────
# Licenses (commercial use): SDXL base 1.0 = CreativeML Open RAIL++-M (allowed,
# with use-based restrictions); SD 1.5 = CreativeML Open RAIL-M (allowed, with
# use-based restrictions). Any LoRA must be checked individually before use.

PROFILES = {
    "sdxl": {"checkpoint": "sd_xl_base_1.0.safetensors", "size": 1024,
             "steps": 25, "cfg": 6.5, "sampler": "dpmpp_2m", "scheduler": "karras"},
    "sd15": {"checkpoint": "v1-5-pruned-emaonly.safetensors", "size": 512,
             "steps": 25, "cfg": 7.0, "sampler": "dpmpp_2m", "scheduler": "karras"},
    "sdxl_lora": {"checkpoint": "sd_xl_base_1.0.safetensors", "size": 1024,
                  "steps": 25, "cfg": 6.5, "sampler": "dpmpp_2m", "scheduler": "karras",
                  "lora": os.environ.get("NS_IMAGE_LORA", ""),
                  "lora_strength": float(os.environ.get("NS_IMAGE_LORA_STRENGTH", "0.8"))},
}
DEFAULT_PROFILE = os.environ.get("NS_IMAGE_PROFILE", "sdxl")

NO_TEXT_SUFFIX = " No text, no words, no letters, no captions, no speech bubbles."
NEGATIVE_PROMPT = (
    "text, words, letters, numbers, typography, caption, speech bubble, writing, "
    "watermark, signature, logo, label, blurry, low quality, distorted, extra limbs, "
    "bad anatomy, deformed hands, photorealistic, photo, 3d render, heavy shading, gradient"
)

# ── Prompt cleaning ──────────────────────────────────────────────────────────

_Q_OPEN, _Q_CLOSE = "'\"‘“", "'\"’”"
_TEXT_VERBS = (
    r"labeled|labelled|reading|that reads|which reads|reads|saying|that says|which says|"
    r"titled|captioned|marked|stamped|printed with|written|with the (?:words?|text|caption|label)|"
    r"with text|displaying(?: the)?(?: text| words)?|showing(?: the)? (?:text|words)|that shows|"
    r"shows|displays|with"
)
_QUOTE = rf"[{_Q_OPEN}](?P<q>[^{_Q_CLOSE}\n]{{1,160}})[{_Q_CLOSE}]"
_TEXT_PHRASE_RE = re.compile(
    rf"\s*,?\s*\b(?:{_TEXT_VERBS})\s*:?\s*{_QUOTE}(?:\s+(?:at the top|on it|in bold))?",
    re.IGNORECASE,
)
# Any remaining quoted span that starts after a non-letter (keeps possessives like "man's").
_QUOTED_RE = re.compile(rf"(?<![A-Za-z]){_QUOTE}(?![A-Za-z])")


def _drop(m: re.Match) -> str:
    """Remove a quoted text span, keeping a sentence end that was inside the quotes."""
    return "." if m.group("q").rstrip().endswith((".", "!", "?")) else ""


def clean_scene(prompt: str) -> str:
    """Drop the style prefix and every instruction that asks for text in the image."""
    s = prompt.replace(cloud.IMAGE_PROMPT_PREFIX, "").replace(cloud.IMAGE_PROMPT_PREFIX.strip(), "")
    s = _TEXT_PHRASE_RE.sub(_drop, s)
    s = _QUOTED_RE.sub(_drop, s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    s = re.sub(r"([,;:])\s*([,.;:])", r"\2", s)
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"\(\s*\)", "", s)
    return s.strip()


def build_positive(scene: str) -> str:
    return cloud.IMAGE_PROMPT_PREFIX + clean_scene(scene) + NO_TEXT_SUFFIX


# ── ComfyUI client ───────────────────────────────────────────────────────────

class ComfyError(RuntimeError):
    pass


def comfy_up() -> bool:
    try:
        return requests.get(f"{COMFY_URL}/system_stats", timeout=5).status_code == 200
    except requests.RequestException:
        return False


def build_workflow(positive: str, seed: int, profile: dict) -> dict:
    wf = json.loads((WORKFLOWS_DIR / "txt2img_api.json").read_text(encoding="utf-8"))
    wf["4"]["inputs"]["ckpt_name"] = profile["checkpoint"]
    wf["6"]["inputs"]["text"] = positive
    wf["7"]["inputs"]["text"] = NEGATIVE_PROMPT
    wf["5"]["inputs"].update(width=profile["size"], height=profile["size"])
    wf["3"]["inputs"].update(seed=seed, steps=profile["steps"], cfg=profile["cfg"],
                             sampler_name=profile["sampler"], scheduler=profile["scheduler"])
    if profile.get("lora"):
        wf["10"] = {"class_type": "LoraLoader", "inputs": {
            "lora_name": profile["lora"], "strength_model": profile["lora_strength"],
            "strength_clip": profile["lora_strength"], "model": ["4", 0], "clip": ["4", 1]}}
        wf["3"]["inputs"]["model"] = ["10", 0]
        wf["6"]["inputs"]["clip"] = ["10", 1]
        wf["7"]["inputs"]["clip"] = ["10", 1]
    return wf


def render(positive: str, seed: int, profile_name: str = DEFAULT_PROFILE,
           timeout: int = 900) -> tuple[Image.Image, float]:
    """Queue one render and wait. Returns (image, seconds of ComfyUI execution)."""
    profile = copy.deepcopy(PROFILES[profile_name])
    if profile_name == "sdxl_lora" and not profile.get("lora"):
        raise ComfyError("sdxl_lora profile needs NS_IMAGE_LORA set")
    wf = build_workflow(positive, seed, profile)
    client_id = uuid.uuid4().hex
    r = requests.post(f"{COMFY_URL}/prompt", json={"prompt": wf, "client_id": client_id}, timeout=30)
    if r.status_code != 200:
        raise ComfyError(f"ComfyUI rejected workflow {r.status_code}: {r.text[:400]}")
    pid = r.json()["prompt_id"]

    t0 = time.perf_counter()
    while True:
        h = requests.get(f"{COMFY_URL}/history/{pid}", timeout=30).json()
        if pid in h:
            entry = h[pid]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise ComfyError(f"ComfyUI execution error: {json.dumps(status)[:600]}")
            if status.get("completed"):
                break
        if time.perf_counter() - t0 > timeout:
            raise ComfyError(f"Render timed out after {timeout}s")
        time.sleep(0.5)

    # Execution time from ComfyUI's own timestamps (excludes queue wait).
    stamps = {m[0]: m[1].get("timestamp") for m in status.get("messages", []) if isinstance(m, list)}
    if stamps.get("execution_start") and stamps.get("execution_success"):
        seconds = round((stamps["execution_success"] - stamps["execution_start"]) / 1000, 2)
    else:
        seconds = round(time.perf_counter() - t0, 2)

    img_meta = entry["outputs"]["9"]["images"][0]
    v = requests.get(f"{COMFY_URL}/view", params={
        "filename": img_meta["filename"], "subfolder": img_meta.get("subfolder", ""),
        "type": img_meta.get("type", "temp")}, timeout=60)
    v.raise_for_status()
    return Image.open(io.BytesIO(v.content)).convert("RGB"), seconds


def free_vram() -> None:
    """Ask ComfyUI to unload models, so Ollama or another profile gets the GPU."""
    try:
        requests.post(f"{COMFY_URL}/free", json={"unload_models": True, "free_memory": True}, timeout=60)
    except requests.RequestException:
        pass


# ── Caption layout ───────────────────────────────────────────────────────────

CANVAS = 1024
FONT_PATH = os.environ.get("NS_CAPTION_FONT", r"C:\Windows\Fonts\georgiab.ttf")
FONT_SIZE = 34
SIDE_MARGIN = 64
TOP_MARGIN = 28
CAPTION_PAD = 26
LINE_SPACING = 1.3
TARGET_KB = 200


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def compose(panel: Image.Image, caption: str) -> Image.Image:
    """Panel on top, caption centered below it, on white, 1024x1024."""
    canvas = Image.new("RGB", (CANVAS, CANVAS), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    max_w = CANVAS - 2 * SIDE_MARGIN
    lines = _wrap(draw, caption.strip(), font, max_w) if caption.strip() else []
    # Balance the lines: narrowest width that keeps the same line count (no orphan words).
    if len(lines) > 1:
        lo, hi = max_w // len(lines), max_w
        while lo < hi:
            mid = (lo + hi) // 2
            if len(_wrap(draw, caption.strip(), font, mid)) <= len(lines):
                hi = mid
            else:
                lo = mid + 1
        lines = _wrap(draw, caption.strip(), font, lo)
    line_h = int(FONT_SIZE * LINE_SPACING)
    band = (len(lines) * line_h + 2 * CAPTION_PAD) if lines else TOP_MARGIN

    side = min(CANVAS - 2 * TOP_MARGIN, CANVAS - band - TOP_MARGIN)
    panel = panel.resize((side, side), Image.LANCZOS)
    canvas.paste(panel, ((CANVAS - side) // 2, TOP_MARGIN))

    y = TOP_MARGIN + side + CAPTION_PAD
    for ln in lines:
        w = draw.textlength(ln, font=font)
        draw.text(((CANVAS - w) / 2, y), ln, font=font, fill=(20, 20, 20))
        y += line_h
    return canvas


def save_jpeg(img: Image.Image, path: Path, target_kb: int = TARGET_KB) -> int:
    """Save at the highest quality that stays near the target size. Returns bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b""
    for q in (92, 90, 88, 85, 82, 78, 74, 70):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        data = buf.getvalue()
        if len(data) <= target_kb * 1024 * 1.15:
            break
    path.write_bytes(data)
    return len(data)


def make_cartoon(scene_prompt: str, caption: str, out_path: Path, seed: int,
                 profile_name: str = DEFAULT_PROFILE) -> dict:
    """Full step: clean prompt, render, compose caption, save. Returns stats."""
    positive = build_positive(scene_prompt)
    panel, seconds = render(positive, seed, profile_name)
    size = save_jpeg(compose(panel, caption), out_path)
    return {"profile": profile_name, "checkpoint": PROFILES[profile_name]["checkpoint"],
            "seed": seed, "render_s": seconds, "bytes": size, "positive": positive,
            "negative": NEGATIVE_PROMPT, "path": str(out_path)}


def main():
    p = argparse.ArgumentParser(description="Render one Neural Strip Lab cartoon")
    p.add_argument("--scene", required=True)
    p.add_argument("--caption", default="")
    p.add_argument("--out", required=True)
    p.add_argument("--profile", default=DEFAULT_PROFILE, choices=sorted(PROFILES))
    p.add_argument("--seed", type=int, default=1001)
    a = p.parse_args()
    if not comfy_up():
        raise SystemExit(f"ComfyUI is not reachable at {COMFY_URL}")
    print(json.dumps(make_cartoon(a.scene, a.caption, Path(a.out), a.seed, a.profile), indent=2))


if __name__ == "__main__":
    main()
