# -*- coding: utf-8 -*-
"""
Neural Strip: shared writing rules, copy checks and judge prompt.

Single source of truth for both pipelines. The lab imports this today.
The cloud pipeline (pipeline/generate.py) switches to importing it when
the cloud rules diff merges. No imports from generate.py here, so there
is no import cycle.
"""

import re

# ── Writer system prompt ─────────────────────────────────────────────────────
# The cloud prompt, with the one-liner writing order, content rules and
# owner copy rules added. Field order in the JSON matches the writing order.

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
  "angle": "the absurd contradiction in the story, in one sentence",
  "scene": "the single moment where that contradiction is visible (what the viewer sees)",
  "speaker": "the one person in the scene who says the caption",
  "setup": "always an empty string",
  "punchline": "what the speaker says (see CAPTION FORMAT)",
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

WRITING ORDER (follow it, one field at a time):
1. angle: find the absurd contradiction in the story, such as what they claim versus what actually happens.
2. scene: pick the one moment where that contradiction is visible in a single panel.
3. speaker: pick one person in that scene.
4. punchline: what that person says in that moment.

CAPTION FORMAT:
- The punchline is one line spoken by the speaker, reacting inside the moment.
- 15 words or fewer, with the twist in the last few words.
- Not narration, not commentary about the news, not a summary, not a restatement of the headline.
- No back-and-forth, no second speaker, no speaker labels, no quotation marks.
- "setup" is always an empty string.

CONTENT RULES for every field except headline and source_url:
- Never name real people, companies, products, apps or brands. Use generic roles and things \
("a tech company", "the chatbot", "an executive").
- No political content of any kind.
- No profanity. Nothing framed around scams, phishing, violence, weapons, the military or crime, \
even when the story is about them. Keep it dry and office-safe.
- Hacking and security topics are fine when the joke is about the absurdity. Never explain how \
to cause harm, and never make victims the butt of the joke.
- Prefer stories whose absurdity works without naming anyone.
- No em-dashes, no hashtags, no emoji, no superlatives. Sentence case.

Output ONLY the JSON object. No markdown, no explanation."""

# ── Copy and format rules (deterministic) ────────────────────────────────────

COPY_FIELDS = ("setup", "punchline", "instagram_caption")
MAX_CAPTION_WORDS = 15
HEADLINE_OVERLAP_MAX = 0.6

_DASH_RE = re.compile(r"—|\s–\s")
_HASHTAG_RE = re.compile(r"(^|\s)#\w+")
_SPEAKER_LABEL_RE = re.compile(r"(^|[.!?]\s+)[A-Z][\w .'-]{0,30}:\s")
_WORD_RE = re.compile(r"[\w'’-]+")
_STOPWORDS = set("""a an the and or but of to in on at for with from by as is are was were be been
it its it's this that these those we our you your they their them he she his her i me my
not no so just now new more most can will would should could has have had do does did""".split())


def strip_wrapping_quotes(text: str) -> str:
    """New Yorker captions sit under the panel without quotation marks."""
    t = text.strip()
    pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
    if len(t) > 1 and t[0] in pairs and t[-1] == pairs[t[0]] and t.count(t[0]) <= 2:
        t = t[1:-1].strip()
    return t


def headline_overlap(punchline: str, headline: str) -> float:
    """Share of the punchline's content words that also appear in the headline."""
    words = {w.lower().strip("'’") for w in _WORD_RE.findall(punchline)} - _STOPWORDS
    if not words:
        return 0.0
    head = {w.lower().strip("'’") for w in _WORD_RE.findall(headline)}
    return len(words & head) / len(words)


def copy_rules_check(concept: dict) -> str:
    """Owner copy rules and the one-liner format. Returns '' when clean, else the reason."""
    for f in COPY_FIELDS:
        v = str(concept.get(f, ""))
        if _DASH_RE.search(v):
            return f"em-dash in {f}"
        if _HASHTAG_RE.search(v):
            return f"hashtag in {f}"
    if str(concept.get("setup", "")).strip():
        return "setup must be empty (one-liner format)"
    punch = str(concept.get("punchline", "")).strip()
    if not punch:
        return "punchline is empty"
    words = len(_WORD_RE.findall(punch))
    if words > MAX_CAPTION_WORDS:
        return f"punchline has {words} words (max {MAX_CAPTION_WORDS})"
    if _SPEAKER_LABEL_RE.search(punch) or len(re.findall(r"[\"“]", punch)) > 2:
        return "punchline is dialogue (one speaker only)"
    overlap = headline_overlap(punch, str(concept.get("headline", "")))
    if overlap >= HEADLINE_OVERLAP_MAX:
        return f"punchline restates the headline ({overlap:.0%} of its words)"
    return ""


# ── Content judge ────────────────────────────────────────────────────────────

# The judge sees the joke only. The source headline is excluded on purpose:
# news headlines routinely name companies and people, the joke must not.
JUDGE_FIELDS = ("angle", "scene", "speaker", "setup", "punchline", "instagram_caption", "image_scene")

JUDGE_SYSTEM = """\
You review single-panel cartoon drafts for a brand-safe humor site about the AI industry.
Check the draft against these rules. Fail it if any rule is broken.

1. No named real people (no names of executives, researchers, politicians, celebrities).
   Generic roles like "a CEO" or "an engineer" are fine.
2. No named real companies, products, apps or brands. Generic terms like
   "a tech company", "the chatbot" or "an AI lab" are fine.
3. No political content: no parties, elections, politicians, partisan issues, governments taking sides.
4. No profanity, slurs, sexual content or graphic violence.
5. Office-safe framing only. Fail any draft framed around scams, fraud, phishing, violence,
   weapons, the military, war or crime, even when the news story is about those things.
   Hacking and security topics are allowed when the joke is about the absurdity.
   Fail any draft that explains how to cause harm, or that makes victims the butt of the joke.
6. Tone is dry and observational. Not corny, not preachy, not mean-spirited.
7. Copy rules for setup, punchline and instagram_caption: no hashtags, no emoji,
   no em-dashes, no superlatives or hype words, sentence case.
8. The punchline must be a line spoken by one person in the scene, reacting inside the moment.
   Fail punchlines that are narration, commentary about the news, a summary of the story,
   or a plain statement of fact with no twist.

Judge only what is written. Do not invent problems.
Return verdict "pass" or "fail", a one-sentence reason, and the list of broken rules
(empty list when passing)."""

JUDGE_USER_TEMPLATE = "Cartoon draft to review:\n\n{draft}"


def judge_draft_text(concept: dict, style_prefix: str = "") -> str:
    """The judge's view of a concept: joke fields only, style prefix removed."""
    view = dict(concept)
    scene = str(concept.get("image_scene") or concept.get("image_prompt", ""))
    view["image_scene"] = scene.replace(style_prefix, "") if style_prefix else scene
    return "\n".join(f"{f}: {view.get(f, '')}" for f in JUDGE_FIELDS)
