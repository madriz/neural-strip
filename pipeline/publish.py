#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip — publish today's cartoon to the website.

Reads pipeline/pending_post.json and pipeline/output_images/YYYY-MM-DD.jpg
(checked out from the daily-output branch by the workflow), copies the
image into website/images/, and prepends a new entry to
website/cartoons.json.

Emits the cartoon date on stdout; all other logs go to stderr.
"""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PENDING_POST = REPO_ROOT / "pipeline" / "pending_post.json"
IMAGES_DIR = REPO_ROOT / "pipeline" / "output_images"
CARTOONS_JSON = REPO_ROOT / "website" / "cartoons.json"
WEBSITE_IMAGES = REPO_ROOT / "website" / "images"

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def format_display_date(date_str: str) -> str:
    y, m, d = date_str.split("-")
    return f"{MONTH_NAMES[int(m)]} {int(d)}, {y}"


def resolve_cartoon_id(pending: dict) -> str:
    generated_at = pending.get("generated_at", "")
    if generated_at and len(generated_at) >= 10:
        return generated_at[:10]
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main() -> None:
    if not PENDING_POST.exists():
        log(f"ERROR: {PENDING_POST} not found")
        sys.exit(1)

    pending = json.loads(PENDING_POST.read_text(encoding="utf-8"))
    cartoon_id = resolve_cartoon_id(pending)

    src_image = IMAGES_DIR / f"{cartoon_id}.jpg"
    if not src_image.exists():
        log(f"ERROR: source image {src_image} not found")
        sys.exit(1)

    WEBSITE_IMAGES.mkdir(parents=True, exist_ok=True)
    dst_image = WEBSITE_IMAGES / f"{cartoon_id}.jpg"
    shutil.copy2(src_image, dst_image)
    log(f"Copied {src_image.name} -> {dst_image}")

    if CARTOONS_JSON.exists():
        cartoons = json.loads(CARTOONS_JSON.read_text(encoding="utf-8"))
    else:
        cartoons = {"cartoons": []}

    entries = cartoons.setdefault("cartoons", [])
    entries[:] = [c for c in entries if c.get("id") != cartoon_id]

    image_path = f"/images/{cartoon_id}.jpg"
    new_entry = {
        "id": cartoon_id,
        "date": cartoon_id,
        "date_display": format_display_date(cartoon_id),
        "headline": pending.get("headline", ""),
        "setup": pending.get("setup", ""),
        "punchline": pending.get("punchline", ""),
        "caption": pending.get("instagram_caption", ""),
        "source_url": pending.get("source_url", ""),
        "image_prompt": pending.get("image_prompt", ""),
        "image": image_path,
        "image_url": image_path,
        "instagram_url": "",
        "likes": 0,
        "dislikes": 0,
    }
    entries.insert(0, new_entry)

    CARTOONS_JSON.write_text(
        json.dumps(cartoons, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log(f"Updated {CARTOONS_JSON} — {len(entries)} cartoons total")
    log(f"Newest: {cartoon_id} — {new_entry['headline']}")

    # Stdout: date only, for the workflow to capture
    print(cartoon_id)


if __name__ == "__main__":
    main()
