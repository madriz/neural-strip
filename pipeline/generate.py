#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip — Daily cartoon concept generator.

Fetches top AI news from RSS feeds, sends headlines to Claude Haiku
to pick the best story for a single-panel humor cartoon, and outputs
structured JSON with the cartoon concept.

Usage:
    python generate.py                     # normal run
    python generate.py --dry-run           # skip API call, print feeds only
    python generate.py --date 2026-10-19   # backfill: only stories from the
                                           # 24h before 7:00 AM Toronto that day

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
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import ftfy
import requests

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

# Backfill runs need at least this many candidate stories in the date window.
MIN_BACKFILL_STORIES = 8
TORONTO = ZoneInfo("America/Toronto")

CLAUDE_MODEL = "claude-haiku-5-5"
# Haiku 5.5 thinks by default (adaptive, effort medium) and thinking tokens
# count toward max_tokens, so leave ample room for the JSON answer after it.
CLAUDE_MAX_TOKENS = 16000

OUTPUT_PATH = Path(__file__).parent / "pending_post.json"
OUTPUT_IMAGES_DIR = Path(__file__).parent / "output_images"

IDEOGRAM_API_URL = "https://api.ideogram.ai/v1/ideogram-v3/generate"

IDEOGRAM_NEGATIVE_PROMPT = (
    "blurry, low quality, distorted text, watermark, signature, "
    "extra limbs, bad anatomy"
)

SYSTEM_PROMPT = """\
You are a veteran single-panel cartoon writer for The New Yorker, \
but your beat is AI and tech. Your humor is dry, observational, and smart. \
Never corny, never preachy. Think Gary Larson meets Silicon Valley.

You will receive today's top AI/tech headlines and summaries. \
Pick the ONE story with the best comedic potential for a single-panel cartoon. \
Do not pick a story that is just a product launch. Pick stories with irony, \
absurdity, or unintended consequences.

Output valid JSON with these fields:
{
  "headline": "the original headline you chose",
  "source_url": "the direct URL of the article you chose",
  "angle": "the comedic angle in one sentence",
  "scene": "visual description of the single-panel cartoon (what the viewer sees)",
  "setup": "caption or dialogue line 1 (if needed)",
  "punchline": "the punchline caption or dialogue",
  "instagram_caption": "dry, understated, 1-2 sentences max",
  "image_prompt": "describe ONLY the scene content (characters, setting, objects, expressions, actions). Do NOT include any style instructions."
}

STRICT RULES for source_url:
- Must be the exact URL from the RSS feed item. Do not fabricate or modify URLs.
- If no URL is available, use empty string "".

STRICT RULES for instagram_caption:
- No hashtags. Zero. Never.
- No emoji. Zero. Never.
- No exclamation marks.
- No promotional language, no superlatives, no hype.
- Dry, understated, observational tone. One or two sentences only.
- Sentence case. The humor speaks for itself.

STRICT RULES for image_prompt:
- Describe only what the viewer sees: characters, setting, objects, expressions, spatial layout.
- Do NOT include style instructions (line art, colors, palette, aesthetic, etc.). Style is applied separately.
- Be specific and visual. The scene must be drawable from this description alone.

Output ONLY the JSON object. No markdown, no explanation."""

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

def backfill_window(date_str: str) -> tuple[datetime, datetime]:
    """The 24 hours before 7:00 AM Toronto on date_str, as UTC datetimes."""
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    end = datetime.combine(day, time(7, 0), tzinfo=TORONTO).astimezone(timezone.utc)
    return end - timedelta(hours=24), end


def entry_published_utc(entry) -> datetime | None:
    """Publish time of a feed entry in UTC, or None if the feed doesn't give one."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def fetch_headlines(window: tuple[datetime, datetime] | None = None) -> list[dict]:
    """Fetch items from all RSS feeds. Returns list of {source, title, summary, link}.

    Without a window, takes the top MAX_ITEMS_PER_FEED items per feed. With a
    (start, end) window, reads every item and keeps only those published in it.
    """
    items = []
    for source_name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            entries = feed.entries if window else feed.entries[:MAX_ITEMS_PER_FEED]
            for entry in entries:
                if window:
                    published = entry_published_utc(entry)
                    if published is None or not (window[0] <= published < window[1]):
                        continue
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
CARTOONS_JSON_PATH = Path(__file__).parent.parent / "website" / "cartoons.json"


def load_published_cartoons() -> list[dict]:
    """Read published cartoons from the local checkout, falling back to GitHub.

    The checkout is preferred: in CI it is main as of this run, whereas the
    raw.githubusercontent.com copy can be minutes stale, which would let
    back-to-back backfill runs miss each other's stories.
    """
    try:
        if CARTOONS_JSON_PATH.exists():
            data = json.loads(CARTOONS_JSON_PATH.read_text(encoding="utf-8"))
        else:
            r = requests.get(CARTOONS_JSON_URL, timeout=10)
            if r.status_code != 200:
                return []
            data = r.json()
        # cartoons.json uses {"cartoons": [...]} wrapper
        cartoons = data.get("cartoons", data) if isinstance(data, dict) else data
        return cartoons if isinstance(cartoons, list) else []
    except Exception:
        return []


def fetch_recent_headlines(days: int | None = 7) -> list[str]:
    """Headlines of cartoons from the last `days` days (every cartoon when None)."""
    cartoons = load_published_cartoons()
    cutoff = ""
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return list(set(
        c.get("headline", "") for c in cartoons
        if c.get("date", "") >= cutoff and c.get("headline")
    ))


# ── Claude API ───────────────────────────────────────────────────────────────

class BadReplyError(Exception):
    """Claude's reply was cut off or wasn't valid JSON. Carries the raw text."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


def generate_cartoon_concept(
    headlines_text: str,
    extra_guidance: str = "",
    cartoon_date: str | None = None,
    dedup_days: int | None = 7,
) -> dict:
    """Send headlines to Claude Haiku and get cartoon concept JSON."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Build system prompt with dedup list
    system = SYSTEM_PROMPT
    recent = fetch_recent_headlines(days=dedup_days)
    if recent:
        dedup_list = "\n".join(f"- {h}" for h in recent)
        system += (
            f"\n\nIMPORTANT: These stories have already been used for cartoons recently. "
            f"Do NOT pick any of these or closely related stories:\n{dedup_list}"
        )
        print(f"  Dedup: {len(recent)} recent headlines excluded")

    if extra_guidance:
        system += f"\n\n{extra_guidance}"

    day = datetime.strptime(cartoon_date, "%Y-%m-%d") if cartoon_date else datetime.now(timezone.utc)
    user_prompt = USER_PROMPT_TEMPLATE.format(date=day.strftime("%B %d, %Y"), headlines=headlines_text)

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    print(
        f"  Claude: stop_reason={response.stop_reason} "
        f"input_tokens={response.usage.input_tokens} "
        f"output_tokens={response.usage.output_tokens}"
    )

    # Haiku 5.5 can open with thinking blocks; the answer is in the text blocks.
    raw = "".join(b.text for b in response.content if b.type == "text").strip()

    if response.stop_reason == "max_tokens":
        raise BadReplyError(f"reply hit max_tokens ({CLAUDE_MAX_TOKENS})", raw)

    # Primary fix: ftfy repairs a wide range of UTF-8 double-encoding issues.
    raw = ftfy.fix_text(raw)

    # Safety net: explicit replacements for patterns ftfy may miss.
    for bad, good in MOJIBAKE_FIXES.items():
        raw = raw.replace(bad, good)

    # Parse JSON (handle potential markdown wrapping)
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        concept = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BadReplyError(f"invalid JSON: {e}", raw) from e

    # Output-side pass: recursively clean every string in the parsed dict.
    concept = fix_encoding(concept)

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

def set_step_output(name: str, value: str) -> None:
    """Expose a value to later GitHub Actions steps (no-op outside Actions)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def iso_date(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%d")
    return value


def main():
    parser = argparse.ArgumentParser(description="Neural Strip cartoon concept generator")
    parser.add_argument("--dry-run", action="store_true", help="Fetch feeds only, skip Claude API")
    parser.add_argument(
        "--date", type=iso_date,
        help="Backfill YYYY-MM-DD: use only stories published in the 24h before 7:00 AM Toronto that day",
    )
    args = parser.parse_args()

    cartoon_date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print("=" * 50)
    print("Neural Strip — Daily Generate")
    print(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    window = None
    if args.date:
        window = backfill_window(args.date)
        print(
            f"Backfill: {args.date} (stories published "
            f"{window[0]:%Y-%m-%d %H:%M} to {window[1]:%Y-%m-%d %H:%M} UTC)"
        )
    print("=" * 50)

    # 1. Fetch RSS headlines
    print("\n[1/3] Fetching RSS feeds...")
    items = fetch_headlines(window)
    print(f"  {len(items)} headlines from {len(RSS_FEEDS)} feeds")

    if args.date:
        # Never reuse a story any published cartoon (backfilled or not) used.
        published = load_published_cartoons()
        used_links = {c["source_url"] for c in published if c.get("source_url")}
        used_titles = {c["headline"].strip().lower() for c in published if c.get("headline")}
        before = len(items)
        items = [
            i for i in items
            if i["link"] not in used_links and i["title"].strip().lower() not in used_titles
        ]
        print(f"  Dedup: {before - len(items)} already-published stories removed, {len(items)} candidates")
        if len(items) < MIN_BACKFILL_STORIES:
            print(
                f"\nNot enough stories for {args.date}: {len(items)} candidates, "
                f"need {MIN_BACKFILL_STORIES}. Skipping this date."
            )
            set_step_output("skipped", "true")
            return
    elif not items:
        print("ERROR: No headlines fetched from any feed", file=sys.stderr)
        sys.exit(1)

    headlines_text = format_headlines(items)

    if args.dry_run:
        print(f"\n--- Headlines (dry run) ---\n{headlines_text[:2000]}")
        print("\n  DRY RUN — skipping Claude API call")
        return

    # 2. Generate cartoon concept via Claude with brand safety retries (up to 3)
    print("\n[2/3] Generating cartoon concept via Claude Haiku...")
    MAX_ATTEMPTS = 3
    BRAND_SAFETY_GUIDANCE = (
        "Previous attempt was flagged for brand safety. Ensure content is "
        "completely brand safe, neutral, and appropriate for all audiences."
    )
    concept = None
    extra_guidance = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            candidate = generate_cartoon_concept(
                headlines_text,
                extra_guidance=extra_guidance,
                cartoon_date=cartoon_date,
                # Backfills dedup against every cartoon, not just the last week.
                dedup_days=None if args.date else 7,
            )
        except BadReplyError as e:
            print(f"  Bad reply on attempt {attempt}/{MAX_ATTEMPTS}: {e}", file=sys.stderr)
            print(f"  Raw reply (first 500 chars): {e.raw[:500]!r}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"ERROR: Claude API call failed: {e}", file=sys.stderr)
            sys.exit(1)

        if brand_safety_check(candidate):
            concept = candidate
            break
        print(f"  Brand safety failed on attempt {attempt}/{MAX_ATTEMPTS}", file=sys.stderr)
        extra_guidance = BRAND_SAFETY_GUIDANCE

    if concept is None:
        print(
            f"ERROR: No usable cartoon concept after {MAX_ATTEMPTS} attempts "
            f"(bad replies or brand safety failures, see above)",
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
    concept["cartoon_date"] = cartoon_date
    concept["feed_count"] = len(items)

    # Generate cartoon image via Ideogram (best-effort; pipeline continues on failure)
    print("\n[3/3] Generating cartoon image via Ideogram...")
    image_prompt = concept.get("image_prompt", "")
    concept["image_local_path"] = generate_image(image_prompt, cartoon_date) if image_prompt else ""

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
