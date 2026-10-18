#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip — Daily cartoon concept generator.

Fetches top AI news from RSS feeds, sends headlines to Claude Haiku
to pick the best story for a single-panel humor cartoon, and outputs
structured JSON with the cartoon concept.

Usage:
    python generate.py              # normal run
    python generate.py --dry-run    # skip API call, print feeds only

Environment:
    ANTHROPIC_API_KEY   — required
    IDEOGRAM_API_KEY    — optional; enables automatic cartoon image generation
"""

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import ftfy
import requests

from lab import rules  # shared writing rules, copy checks and judge prompt

# ── Encoding helpers ─────────────────────────────────────────────────────────

MOJIBAKE_FIXES = {
    'Ã©': 'é', 'Ã¨': 'è', 'Ã ': 'à',
    'Ã®': 'î', 'Ã´': 'ô', 'Ã»': 'û',
    'Ã§': 'ç', 'Ã¦': 'æ', 'Ã¼': 'ü',
    'Ã¶': 'ö', 'Ã¤': 'ä', 'Ã±': 'ñ',
    'â€™': "'", 'â€œ': '"', 'â€\x9d': '"',
    'â€"': '—', 'â€"': '–', 'â€¦': '…',
    'Â°': '°', 'Â£': '£', 'Â©': '©',
}


def fix_encoding(obj):
    """Recursively apply ftfy to every string in a nested dict/list."""
    if isinstance(obj, str):
        try:
            return ftfy.fix_text(obj)
        except Exception:
            return obj
    elif isinstance(obj, dict):
        return {k: fix_encoding(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fix_encoding(i) for i in obj]
    return obj

# ── Constants ────────────────────────────────────────────────────────────────

RSS_FEEDS = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab"),
    ("Wired AI", "https://www.wired.com/feed/tag/artificial-intelligence/rss"),
    ("Hacker News", "https://news.ycombinator.com/rss"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ("Reuters Technology", "https://feeds.reuters.com/reuters/technologyNews"),
    ("BBC Technology", "http://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("The Register", "https://www.theregister.com/headlines.atom"),
    ("ZDNet AI", "https://www.zdnet.com/topic/artificial-intelligence/rss.xml"),
    ("IEEE Spectrum AI", "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss"),
    ("AI News", "https://www.artificialintelligence-news.com/feed/"),
    ("Towards Data Science", "https://towardsdatascience.com/feed"),
    ("Import AI (Jack Clark)", "https://importai.substack.com/feed"),
]

MAX_ITEMS_PER_FEED = 6

OUTPUT_PATH = Path(__file__).parent / "pending_post.json"
OUTPUT_IMAGES_DIR = Path(__file__).parent / "output_images"

IDEOGRAM_API_URL = "https://api.ideogram.ai/v1/ideogram-v3/generate"

IDEOGRAM_NEGATIVE_PROMPT = (
    "blurry, low quality, distorted text, watermark, signature, "
    "extra limbs, bad anatomy"
)

# Writer prompt: shared with Neural Strip Lab so both pipelines run the same
# one-liner writing order, content rules and owner copy rules.
SYSTEM_PROMPT = rules.SYSTEM_PROMPT

IMAGE_PROMPT_PREFIX = (
    "Single-panel cartoon, clean line art, thick black outlines, "
    "muted blue-gray pastel colors, white background, New Yorker "
    "magazine aesthetic, flat colors, no shading, no gradients, "
    "no signature. "
)

USER_PROMPT_TEMPLATE = """\
Here are today's top AI/tech headlines ({date}):

{headlines}

Pick the best one for a single-panel humor cartoon and output the JSON."""


# ── RSS Fetching ─────────────────────────────────────────────────────────────

def fetch_headlines() -> list[dict]:
    """Fetch top items from all RSS feeds. Returns list of {source, title, summary, link}."""
    items = []
    for source_name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:MAX_ITEMS_PER_FEED]:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                # Strip HTML from summary
                if "<" in summary:
                    import re
                    summary = re.sub(r"<[^>]+>", "", summary).strip()
                # Truncate long summaries
                if len(summary) > 300:
                    summary = summary[:297] + "..."
                link = entry.get("link", "")
                if title:
                    items.append({
                        "source": source_name,
                        "title": title,
                        "summary": summary,
                        "link": link,
                    })
        except Exception as e:
            print(f"  WARN: Failed to fetch {source_name}: {e}", file=sys.stderr)
    return items


def format_headlines(items: list[dict]) -> str:
    """Format headline items into a numbered list for the prompt."""
    lines = []
    for i, item in enumerate(items, 1):
        line = f"{i}. [{item['source']}] {item['title']}"
        if item["summary"]:
            line += f"\n   {item['summary']}"
        if item.get("link"):
            line += f"\n   URL: {item['link']}"
        lines.append(line)
    return "\n\n".join(lines)


# ── Recent story deduplication ───────────────────────────────────────────────

CARTOONS_JSON_URL = "https://raw.githubusercontent.com/madriz/neural-strip/main/website/cartoons.json"


def fetch_recent_headlines(days: int = 7) -> list[str]:
    """Fetch headlines from cartoons.json to avoid repeating recent stories."""
    try:
        r = requests.get(CARTOONS_JSON_URL, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        # cartoons.json uses {"cartoons": [...]} wrapper
        cartoons = data.get("cartoons", data) if isinstance(data, dict) else data
        if not isinstance(cartoons, list):
            return []
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        return list(set(
            c.get("headline", "") for c in cartoons
            if c.get("date", "") >= cutoff and c.get("headline")
        ))
    except Exception:
        return []


# ── Claude API ───────────────────────────────────────────────────────────────

def generate_cartoon_concept(headlines_text: str, extra_guidance: str = "") -> dict:
    """Send headlines to Claude Haiku and get cartoon concept JSON."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Build system prompt with dedup list
    system = SYSTEM_PROMPT
    recent = fetch_recent_headlines(days=7)
    if recent:
        dedup_list = "\n".join(f"- {h}" for h in recent)
        system += (
            f"\n\nIMPORTANT: These stories have already been used for cartoons recently. "
            f"Do NOT pick any of these or closely related stories:\n{dedup_list}"
        )
        print(f"  Dedup: {len(recent)} recent headlines excluded")

    if extra_guidance:
        system += f"\n\n{extra_guidance}"

    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    user_prompt = USER_PROMPT_TEMPLATE.format(date=today, headlines=headlines_text)

    response = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Primary fix: ftfy repairs a wide range of UTF-8 double-encoding issues.
    raw = ftfy.fix_text(raw)

    # Safety net: explicit replacements for patterns ftfy may miss.
    for bad, good in MOJIBAKE_FIXES.items():
        raw = raw.replace(bad, good)

    # Parse JSON (handle potential markdown wrapping)
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    concept = json.loads(raw)

    # Output-side pass: recursively clean every string in the parsed dict.
    concept = fix_encoding(concept)
    concept["punchline"] = rules.strip_wrapping_quotes(str(concept.get("punchline", "")))

    # Prepend locked style prefix to image_prompt
    if concept.get("image_prompt"):
        concept["image_prompt"] = IMAGE_PROMPT_PREFIX + concept["image_prompt"]

    return concept


# ── Brand safety filter ──────────────────────────────────────────────────────

BRAND_SAFETY_KEYWORDS: dict[str, list[str]] = {
    "profanity": [
        "fuck", "fucking", "shit", "bitch", "bastard", "asshole",
        "cunt", "cock", "dick", "pussy", "whore", "slut", "fag",
    ],
    "hate": [
        "nigger", "kike", "spic", "chink", "gook", "wetback",
        "tranny", "faggot", "retard",
    ],
    "violence": [
        "murder", "murdered", "rape", "raped", "kill", "killing",
        "slaughter", "massacre", "torture", "lynch",
    ],
    "sexual": [
        "porn", "porno", "pornography", "masturbate", "orgasm",
        "blowjob", "horny", "erotic", "nude", "naked", "anal",
        "nipple", "penis", "vagina",
    ],
    "political": [
        "trump", "biden", "harris", "obama", "clinton", "pence",
        "desantis", "newsom", "vance", "ramaswamy", "kennedy",
        "maga",
    ],
}


BRAND_SAFETY_FIELDS = ("headline", "setup", "punchline", "instagram_caption")


def brand_safety_check(concept: dict) -> bool:
    """Scan the user-facing text fields for disallowed content.

    Returns True when content is clean, False when flagged.
    """
    combined = " ".join(str(concept.get(f, "")) for f in BRAND_SAFETY_FIELDS).lower()
    for category, terms in BRAND_SAFETY_KEYWORDS.items():
        for term in terms:
            if re.search(rf"\b{re.escape(term)}\b", combined):
                print(
                    f"BRAND SAFETY: flagged content detected — regenerating "
                    f"(category={category}, term={term!r})",
                    file=sys.stderr,
                )
                return False
    return True


# ── Content judge (Haiku, same prompt as Neural Strip Lab) ───────────────────

def judge_concept(concept: dict) -> dict:
    """Judge pass over the joke fields only. Returns {"verdict": "pass"|"fail", "reason", "violations"}."""
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=300,
        temperature=0,
        system=rules.JUDGE_SYSTEM + '\n\nOutput ONLY a JSON object: '
               '{"verdict": "pass" or "fail", "reason": "...", "violations": ["..."]}',
        messages=[{"role": "user", "content": rules.JUDGE_USER_TEMPLATE.format(
            draft=rules.judge_draft_text(concept, IMAGE_PROMPT_PREFIX))}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    verdict = json.loads(raw)
    verdict["verdict"] = "pass" if verdict.get("verdict") == "pass" else "fail"
    return verdict


# ── Generation with checks, retries and story fallback ───────────────────────

MAX_ATTEMPTS = 3   # per story
MAX_STORIES = 2    # if a story fails every attempt, try the next-best story once

BRAND_SAFETY_GUIDANCE = (
    "Previous attempt was flagged for brand safety. Ensure content is "
    "completely brand safe, neutral, and appropriate for all audiences."
)


def generate_checked_concept(headlines_text: str) -> tuple[dict | None, list[dict]]:
    """
    Up to MAX_ATTEMPTS per story, up to MAX_STORIES stories. Every attempt runs the
    keyword filter, the copy and format rules, and the Haiku judge.
    Returns (concept or None, per-attempt log). Invalid JSON or API errors still exit.
    """
    log: list[dict] = []
    excluded: list[str] = []
    for story in range(1, MAX_STORIES + 1):
        locked = ""
        guidance = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            parts = []
            if excluded:
                parts.append("These stories could not be made brand safe. Do NOT pick them:\n"
                             + "\n".join(f"- {h}" for h in excluded))
            if locked:
                parts.append(f"Write about this same story again: {locked}")
            if guidance:
                parts.append(guidance)
            try:
                candidate = generate_cartoon_concept(headlines_text, extra_guidance="\n\n".join(parts))
            except json.JSONDecodeError as e:
                print(f"ERROR: Claude returned invalid JSON: {e}", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                print(f"ERROR: Claude API call failed: {e}", file=sys.stderr)
                sys.exit(1)
            locked = locked or candidate.get("headline", "")
            rec = {"story": story, "attempt": attempt, "headline": candidate.get("headline", ""),
                   "punchline": candidate.get("punchline", "")}
            log.append(rec)
            tag = f"story {story}/{MAX_STORIES}, attempt {attempt}/{MAX_ATTEMPTS}"

            if not brand_safety_check(candidate):
                rec["failed"] = "keyword filter"
                print(f"  Brand safety failed ({tag})", file=sys.stderr)
                guidance = BRAND_SAFETY_GUIDANCE
                continue

            reason = rules.copy_rules_check(candidate)
            if reason:
                rec["failed"] = reason
                print(f"  Copy rules failed ({tag}): {reason}", file=sys.stderr)
                guidance = f"Previous attempt was rejected: {reason}. Follow CAPTION FORMAT and CONTENT RULES."
                continue

            try:
                verdict = judge_concept(candidate)
            except Exception as e:
                rec["failed"] = f"judge error: {e}"
                print(f"  Judge call failed ({tag}): {e}", file=sys.stderr)
                guidance = ""
                continue
            rec["judge"] = verdict
            if verdict["verdict"] != "pass":
                rec["failed"] = f"judge: {verdict.get('reason', '')}"
                print(f"  Judge failed ({tag}): {verdict.get('reason', '')}", file=sys.stderr)
                guidance = (
                    f"Previous attempt was rejected by the content reviewer: {verdict.get('reason', '')} "
                    f"Broken rules: {'; '.join(verdict.get('violations', []))}. Fix this in the new draft."
                )
                continue

            candidate["judge"] = verdict
            candidate["attempts"] = log
            return candidate, log

        if locked:
            excluded.append(locked)
            print(f"  Story failed {MAX_ATTEMPTS} attempts, trying the next-best story: {locked}",
                  file=sys.stderr)
    return None, log


# ── Ideogram image generation ────────────────────────────────────────────────

def generate_image(image_prompt: str, date_str: str) -> str:
    """
    Generate a cartoon image via Ideogram v3 and save as JPEG.
    Returns local file path on success, empty string on any failure.

    Ideogram image URLs expire quickly — download happens immediately.
    The v3 API returns PNG; we convert to JPEG to keep the site's .jpg contract.

    Cost: ~$0.06 per cartoon at QUALITY. Under $2/month at one/day.

    style_type: "AUTO" — the v3 enum no longer exposes "ILLUSTRATION";
    AUTO lets the prompt (which already carries the full New Yorker style)
    drive the render.

    seed: omitted — the API rejects null, and omission gives a random seed,
    which is the behavior we want for daily variety.
    """
    api_key = os.environ.get("IDEOGRAM_API_KEY", "")
    if not api_key:
        print("  WARN: IDEOGRAM_API_KEY not set — skipping image generation", file=sys.stderr)
        return ""

    OUTPUT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_IMAGES_DIR / f"{date_str}.jpg"

    try:
        resp = requests.post(
            IDEOGRAM_API_URL,
            headers={"Api-Key": api_key, "Content-Type": "application/json"},
            json={
                "prompt": image_prompt,
                "rendering_speed": "QUALITY",
                "style_type": "AUTO",
                "aspect_ratio": "1x1",
                "magic_prompt_option": "OFF",
                "negative_prompt": IDEOGRAM_NEGATIVE_PROMPT,
            },
            timeout=180,
        )
        if resp.status_code != 200:
            print(f"  ERROR: Ideogram API {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
            return ""

        image_url = resp.json()["data"][0]["url"]
    except Exception as e:
        print(f"  ERROR: Ideogram API call failed: {e}", file=sys.stderr)
        return ""

    try:
        img_resp = requests.get(image_url, timeout=60)
        if img_resp.status_code != 200:
            print(f"  ERROR: Image download {img_resp.status_code}", file=sys.stderr)
            return ""

        from PIL import Image
        img = Image.open(io.BytesIO(img_resp.content))
        if img.mode != "RGB":
            img = img.convert("RGB")
        # Web resolution: 1024x1024px — optimized for site speed
        # Print resolution: handled separately via print_prep.py (on-demand)
        img.save(output_path, "JPEG", quality=92)
        print(f"  Saved image to {output_path} ({output_path.stat().st_size:,} bytes)")
        return str(output_path)
    except Exception as e:
        print(f"  ERROR: Image download/save failed: {e}", file=sys.stderr)
        return ""


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Neural Strip cartoon concept generator")
    parser.add_argument("--dry-run", action="store_true", help="Fetch feeds only, skip Claude API")
    args = parser.parse_args()

    print("=" * 50)
    print("Neural Strip — Daily Generate")
    print(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 50)

    # 1. Fetch RSS headlines
    print("\n[1/3] Fetching RSS feeds...")
    items = fetch_headlines()
    print(f"  {len(items)} headlines from {len(RSS_FEEDS)} feeds")

    if not items:
        print("ERROR: No headlines fetched from any feed", file=sys.stderr)
        sys.exit(1)

    headlines_text = format_headlines(items)

    if args.dry_run:
        print(f"\n--- Headlines (dry run) ---\n{headlines_text[:2000]}")
        print("\n  DRY RUN — skipping Claude API call")
        return

    # 2. Generate cartoon concept via Claude: keyword filter, copy rules and judge,
    #    up to 3 attempts per story, falling back to the next-best story once
    print("\n[2/3] Generating cartoon concept via Claude Haiku...")
    concept, _log = generate_checked_concept(headlines_text)

    if concept is None:
        print(
            f"ERROR: Content failed safety, copy or judge checks after {MAX_ATTEMPTS} attempts "
            f"on each of {MAX_STORIES} stories",
            file=sys.stderr,
        )
        sys.exit(1)

    # Fallback: if source_url missing, find it from the RSS items by matching headline
    if not concept.get("source_url"):
        chosen = concept.get("headline", "").lower()
        for item in items:
            if item["title"].lower() in chosen or chosen in item["title"].lower():
                concept["source_url"] = item.get("link", "")
                break
        if not concept.get("source_url"):
            concept["source_url"] = ""

    # Add metadata
    concept["generated_at"] = datetime.now(timezone.utc).isoformat()
    concept["feed_count"] = len(items)

    # Generate cartoon image via Ideogram (best-effort; pipeline continues on failure)
    print("\n[3/3] Generating cartoon image via Ideogram...")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    image_prompt = concept.get("image_prompt", "")
    concept["image_local_path"] = generate_image(image_prompt, today_str) if image_prompt else ""

    # Print result
    pretty = json.dumps(concept, indent=2, ensure_ascii=False)
    print(f"\n{pretty}")

    # Save to file
    OUTPUT_PATH.write_text(pretty, encoding="utf-8")
    print(f"\nSaved to {OUTPUT_PATH}")

    # Save timestamped copy (for daily-output branch history)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ts_path = OUTPUT_PATH.parent / f"pending_post_{ts}.json"
    ts_path.write_text(pretty, encoding="utf-8")
    print(f"Saved timestamped copy to {ts_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
