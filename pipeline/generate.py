#!/usr/bin/env python3
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
    SUPABASE_URL        — for future storage (not used yet)
    SUPABASE_KEY        — for future storage (not used yet)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser

# ── Constants ────────────────────────────────────────────────────────────────

RSS_FEEDS = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/technology-lab"),
    ("Wired AI", "https://www.wired.com/feed/tag/artificial-intelligence/rss"),
    ("Hacker News", "https://news.ycombinator.com/rss"),
]

MAX_ITEMS_PER_FEED = 10

OUTPUT_PATH = Path(__file__).parent / "pending_post.json"

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
  "angle": "the comedic angle in one sentence",
  "scene": "visual description of the single-panel cartoon (what the viewer sees)",
  "setup": "caption or dialogue line 1 (if needed)",
  "punchline": "the punchline caption or dialogue",
  "instagram_caption": "dry, understated, 1-2 sentences max",
  "image_prompt": "describe ONLY the scene content (characters, setting, objects, expressions, actions). Do NOT include any style instructions."
}

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
        lines.append(line)
    return "\n\n".join(lines)


# ── Claude API ───────────────────────────────────────────────────────────────

def generate_cartoon_concept(headlines_text: str) -> dict:
    """Send headlines to Claude Haiku and get cartoon concept JSON."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    user_prompt = USER_PROMPT_TEMPLATE.format(date=today, headlines=headlines_text)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Parse JSON (handle potential markdown wrapping)
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    concept = json.loads(raw)

    # Prepend locked style prefix to image_prompt
    if concept.get("image_prompt"):
        concept["image_prompt"] = IMAGE_PROMPT_PREFIX + concept["image_prompt"]

    return concept


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
    print("\n[1/2] Fetching RSS feeds...")
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

    # 2. Generate cartoon concept via Claude
    print("\n[2/2] Generating cartoon concept via Claude Haiku...")
    try:
        concept = generate_cartoon_concept(headlines_text)
    except json.JSONDecodeError as e:
        print(f"ERROR: Claude returned invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Claude API call failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Add metadata
    concept["generated_at"] = datetime.now(timezone.utc).isoformat()
    concept["feed_count"] = len(items)

    # Print result
    pretty = json.dumps(concept, indent=2, ensure_ascii=False)
    print(f"\n{pretty}")

    # Save to file
    OUTPUT_PATH.write_text(pretty, encoding="utf-8")
    print(f"\nSaved to {OUTPUT_PATH}")

    print("\nDone.")


if __name__ == "__main__":
    main()
