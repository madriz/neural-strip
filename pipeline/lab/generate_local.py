#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip Lab: local cartoon concept generator.

Same feeds, dedup, system prompt and keyword filter as the cloud pipeline
(imported from pipeline/generate.py, which is never modified). The model
runs locally through Ollama with JSON-schema constrained output.

Safety, three layers, all run on every attempt:
  (a) the cloud keyword filter (brand_safety_check) plus owner copy rules
  (b) an LLM judge over the joke fields only (never the source headline)
  (c) optional Llama Guard classifier (--guard)
Up to 3 attempts; each retry carries the previous failure reason.
After 3 failures: LabFailure with every reason logged.

Usage:
    python generate_local.py                         # independent: model picks a story
    python generate_local.py --headline "..."        # write a joke for one story
    python generate_local.py --guard                 # also run Llama Guard
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import ftfy

import config  # noqa: F401  (puts pipeline/ on sys.path)
from config import (GUARD_MODEL, JUDGE_MODEL, LAB_HOUSE_RULES, LAB_THINK, MAX_ATTEMPTS,
                    OUTPUT_DIR, SITE_LAB_JSON, TEXT_MODEL, TEXT_TEMPERATURE)
import generate as cloud  # the cloud pipeline, reused read-only
import ollama_client as ollama

# ── Structured output schemas ────────────────────────────────────────────────

CONCEPT_FIELDS = ("headline", "source_url", "angle", "scene", "setup",
                  "punchline", "instagram_caption", "image_prompt")

CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {f: {"type": "string"} for f in CONCEPT_FIELDS},
    "required": list(CONCEPT_FIELDS),
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "reason": {"type": "string"},
        "violations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "reason", "violations"],
}

# The judge sees the joke only. The source headline is excluded on purpose:
# news headlines routinely name companies and people, the joke must not.
JUDGE_FIELDS = ("angle", "scene", "setup", "punchline", "instagram_caption", "image_scene")

JUDGE_SYSTEM = """\
You review single-panel cartoon drafts for a brand-safe humor site about the AI industry.
Check the draft against these rules. Fail it if any rule is broken.

1. No named real people (no names of executives, researchers, politicians, celebrities).
   Generic roles like "a CEO" or "an engineer" are fine.
2. No named real companies, products, apps or brands. Generic terms like
   "a tech company", "the chatbot" or "an AI lab" are fine.
3. No political content: no parties, elections, politicians, partisan issues, governments taking sides.
4. No profanity, slurs, sexual content or graphic violence.
5. Tone is dry and observational. Not corny, not preachy, not mean-spirited.
6. Copy rules for setup, punchline and instagram_caption: no hashtags, no emoji,
   no em-dashes, no superlatives or hype words, sentence case.

Judge only what is written. Do not invent problems.
Return verdict "pass" or "fail", a one-sentence reason, and the list of broken rules
(empty list when passing)."""

# Appended to the writer prompt only when NS_LAB_HOUSE_RULES=1.
HOUSE_RULES = """CONTENT RULES for every field except headline and source_url:
- Never name real people, companies, products, apps or brands. Use generic roles and things instead ("a tech company", "the chatbot", "an executive").
- No political content of any kind.
- The punchline must be a joke, not a restatement of the headline."""

JUDGE_USER_TEMPLATE = "Cartoon draft to review:\n\n{draft}"

# ── Owner copy rules, deterministic part ─────────────────────────────────────

COPY_FIELDS = ("setup", "punchline", "instagram_caption")
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]"
)
HASHTAG_RE = re.compile(r"(^|\s)#\w+")


class LabFailure(RuntimeError):
    """Raised when all attempts fail. Carries the per-attempt log."""

    def __init__(self, message: str, attempts: list[dict]):
        super().__init__(message)
        self.attempts = attempts


def fix_dashes(concept: dict) -> list[str]:
    """Replace em and en dashes in copy fields. Returns the fields changed."""
    changed = []
    for f in COPY_FIELDS + ("angle", "scene"):
        v = concept.get(f, "")
        nv = re.sub(r"\s*[—–]\s*", ", ", v)
        if nv != v:
            concept[f] = nv
            changed.append(f)
    return changed


def copy_rules_check(concept: dict) -> str:
    """Hard copy rules that cannot be auto-fixed. Returns '' when clean."""
    for f in COPY_FIELDS:
        v = concept.get(f, "")
        if HASHTAG_RE.search(v):
            return f"hashtag in {f}"
        if EMOJI_RE.search(v):
            return f"emoji in {f}"
    if "!" in concept.get("instagram_caption", ""):
        return "exclamation mark in instagram_caption"
    return ""


def keyword_check(concept: dict) -> str:
    """Layer (a): the cloud filter, unchanged. Returns '' when clean."""
    if cloud.brand_safety_check(concept):
        return ""
    return "cloud keyword filter flagged the draft (see stderr for term)"


# ── Layer (b): LLM judge ─────────────────────────────────────────────────────

def judge(concept: dict) -> tuple[dict, dict]:
    draft = "\n".join(f"{f}: {concept.get(f, '')}" for f in JUDGE_FIELDS)
    verdict, stats = ollama.chat_json(
        JUDGE_MODEL, JUDGE_SYSTEM, JUDGE_USER_TEMPLATE.format(draft=draft),
        JUDGE_SCHEMA, temperature=0.0, num_ctx=4096,
    )
    verdict["verdict"] = "pass" if verdict.get("verdict") == "pass" else "fail"
    return verdict, stats


# ── Layer (c): Llama Guard ───────────────────────────────────────────────────

def guard(concept: dict) -> tuple[dict, dict]:
    text = "\n".join(concept.get(f, "") for f in JUDGE_FIELDS)
    res = ollama.chat(GUARD_MODEL, "", text, temperature=0.0, num_ctx=4096)
    out = res["content"].strip().lower()
    safe = out.startswith("safe")
    categories = re.findall(r"s\d+", out) if not safe else []
    stats = {k: v for k, v in res.items() if k != "content"}
    return {"verdict": "pass" if safe else "fail", "categories": categories}, stats


# ── Generation ───────────────────────────────────────────────────────────────

def _postprocess(raw: dict) -> dict:
    """Mirror the cloud clean-up: ftfy, mojibake table, locked style prefix."""
    concept = {}
    for f in CONCEPT_FIELDS:
        v = str(raw.get(f, "") or "").strip()
        v = ftfy.fix_text(v)
        for bad, good in cloud.MOJIBAKE_FIXES.items():
            v = v.replace(bad, good)
        concept[f] = v
    concept["image_scene"] = concept["image_prompt"]
    if concept["image_prompt"]:
        concept["image_prompt"] = cloud.IMAGE_PROMPT_PREFIX + concept["image_prompt"]
    return concept


def generate_with_safety(system: str, user_prompt: str, *, use_guard: bool = False,
                         forced_story: dict | None = None) -> dict:
    """
    Run generate -> checks up to MAX_ATTEMPTS times.
    Returns the passing concept with a `lab` block of timings and verdicts.
    """
    attempts: list[dict] = []
    timings: dict[str, float] = {}
    guidance = ""

    for n in range(1, MAX_ATTEMPTS + 1):
        rec: dict = {"attempt": n}
        attempts.append(rec)
        sys_prompt = system + (f"\n\n{guidance}" if guidance else "")

        try:
            raw, stats = ollama.chat_json(TEXT_MODEL, sys_prompt, user_prompt,
                                          CONCEPT_SCHEMA, temperature=TEXT_TEMPERATURE,
                                          think=LAB_THINK)
        except ollama.OllamaError as e:
            rec.update(stage="generate", reason=str(e))
            guidance = "Previous attempt failed: output must be a single valid JSON object."
            continue
        timings[f"generate_attempt_{n}"] = stats["seconds"]
        rec["generate_tokens"] = {"prompt": stats["prompt_tokens"], "output": stats["output_tokens"]}

        concept = _postprocess(raw)
        if forced_story:
            concept["headline"] = forced_story["title"]
            concept["source_url"] = forced_story.get("link", "")
        rec["dash_fixes"] = fix_dashes(concept)
        rec["draft"] = {f: concept.get(f, "") for f in ("headline",) + JUDGE_FIELDS}

        # (a) keyword filter + copy rules
        reason = keyword_check(concept) or copy_rules_check(concept)
        if reason:
            rec.update(stage="keyword", reason=reason)
            guidance = (f"Previous attempt was rejected: {reason}. Ensure content is completely "
                        f"brand safe, neutral, and appropriate for all audiences.")
            continue

        # (b) LLM judge
        try:
            verdict, jstats = judge(concept)
        except ollama.OllamaError as e:
            rec.update(stage="judge", reason=f"judge error: {e}")
            continue
        timings[f"judge_attempt_{n}"] = jstats["seconds"]
        rec["judge"] = verdict
        if verdict["verdict"] != "pass":
            rec.update(stage="judge", reason=verdict.get("reason", ""))
            guidance = (f"Previous attempt was rejected by the content reviewer: "
                        f"{verdict.get('reason', '')} Broken rules: "
                        f"{'; '.join(verdict.get('violations', []))}. Fix this in the new draft.")
            continue

        # (c) optional Llama Guard
        gverdict = None
        if use_guard:
            try:
                gverdict, gstats = guard(concept)
            except ollama.OllamaError as e:
                rec.update(stage="guard", reason=f"guard error: {e}")
                continue
            timings[f"guard_attempt_{n}"] = gstats["seconds"]
            rec["guard"] = gverdict
            if gverdict["verdict"] != "pass":
                rec.update(stage="guard", reason=f"Llama Guard flagged {gverdict['categories']}")
                guidance = ("Previous attempt was flagged by a safety classifier. Ensure content is "
                            "completely brand safe, neutral, and appropriate for all audiences.")
                continue

        rec["stage"] = "passed"
        concept["lab"] = {
            "text_model": TEXT_MODEL,
            "judge_model": JUDGE_MODEL,
            "guard_model": GUARD_MODEL if use_guard else None,
            "think": LAB_THINK,
            "house_rules": LAB_HOUSE_RULES,
            "attempts_used": n,
            "attempts": attempts,
            "judge": verdict,
            "guard": gverdict,
            "timings_s": timings,
        }
        return concept

    reasons = "; ".join(f"#{a['attempt']} {a.get('stage')}: {a.get('reason', '')}" for a in attempts)
    raise LabFailure(f"All {MAX_ATTEMPTS} attempts failed: {reasons}", attempts)


def lab_recent_headlines(days: int = 7) -> list[str]:
    """Headlines the lab itself used recently (independent mode dedup)."""
    try:
        data = json.loads(SITE_LAB_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return sorted({
        (e.get("local") or {}).get("headline", "")
        for e in data.get("entries", [])
        if e.get("date", "") >= cutoff and e.get("kind") == "independent"
    } - {""})


def build_system_prompt(dedup: list[str]) -> str:
    """Cloud system prompt plus the cloud dedup block, byte for byte."""
    system = cloud.SYSTEM_PROMPT
    if dedup:
        dedup_list = "\n".join(f"- {h}" for h in dedup)
        system += (
            f"\n\nIMPORTANT: These stories have already been used for cartoons recently. "
            f"Do NOT pick any of these or closely related stories:\n{dedup_list}"
        )
    return system


def run_independent(use_guard: bool = False) -> dict:
    """The local model picks its own story from today's feeds."""
    t_all = time.perf_counter()
    t0 = time.perf_counter()
    items = cloud.fetch_headlines()
    t_fetch = round(time.perf_counter() - t0, 2)
    if not items:
        raise LabFailure("No headlines fetched from any feed", [])

    t0 = time.perf_counter()
    dedup = sorted(set(cloud.fetch_recent_headlines(days=7)) | set(lab_recent_headlines(7)))
    t_dedup = round(time.perf_counter() - t0, 2)

    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    user_prompt = cloud.USER_PROMPT_TEMPLATE.format(date=today, headlines=cloud.format_headlines(items))
    try:
        concept = generate_with_safety(build_system_prompt(dedup), user_prompt, use_guard=use_guard)
    finally:
        _unload_all(use_guard)

    # Same source_url fallback as the cloud pipeline
    if not concept.get("source_url"):
        chosen = concept.get("headline", "").lower()
        for item in items:
            if item["title"].lower() in chosen or chosen in item["title"].lower():
                concept["source_url"] = item.get("link", "")
                break

    concept["generated_at"] = datetime.now(timezone.utc).isoformat()
    concept["feed_count"] = len(items)
    concept["lab"].update(mode="independent", dedup_count=len(dedup))
    concept["lab"]["timings_s"] = {"fetch_feeds": t_fetch, "dedup": t_dedup,
                                   **concept["lab"]["timings_s"],
                                   "total": round(time.perf_counter() - t_all, 2)}
    return concept


def run_for_story(story: dict, date_label: str | None = None, use_guard: bool = False,
                  unload_after: bool = True) -> dict:
    """
    Write a joke for one given story (paired and backfill modes).
    story: {"title", "summary"?, "link"?, "source"?}
    """
    t_all = time.perf_counter()
    item = {"source": story.get("source", "News"), "title": story["title"],
            "summary": story.get("summary", ""), "link": story.get("link", "")}
    date_label = date_label or datetime.now(timezone.utc).strftime("%B %d, %Y")
    user_prompt = cloud.USER_PROMPT_TEMPLATE.format(date=date_label,
                                                    headlines=cloud.format_headlines([item]))
    try:
        concept = generate_with_safety(cloud.SYSTEM_PROMPT, user_prompt,
                                       use_guard=use_guard, forced_story=item)
    finally:
        if unload_after:
            _unload_all(use_guard)
    concept["generated_at"] = datetime.now(timezone.utc).isoformat()
    concept["lab"]["mode"] = "story"
    concept["lab"]["timings_s"]["total"] = round(time.perf_counter() - t_all, 2)
    return concept


def _unload_all(use_guard: bool) -> None:
    """keep_alive 0 on every model this run touched, so VRAM is free for ComfyUI."""
    for m in {TEXT_MODEL, JUDGE_MODEL} | ({GUARD_MODEL} if use_guard else set()):
        ollama.unload(m)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Neural Strip Lab local concept generator")
    p.add_argument("--headline", help="write a joke for this headline instead of picking a story")
    p.add_argument("--summary", default="")
    p.add_argument("--url", default="")
    p.add_argument("--guard", action="store_true", help="also run the Llama Guard classifier")
    p.add_argument("--out", help="output JSON path")
    args = p.parse_args()

    print(f"Neural Strip Lab | text={TEXT_MODEL} judge={JUDGE_MODEL}"
          f"{' guard=' + GUARD_MODEL if args.guard else ''}")
    try:
        if args.headline:
            concept = run_for_story({"title": args.headline, "summary": args.summary,
                                     "link": args.url}, use_guard=args.guard)
        else:
            concept = run_independent(use_guard=args.guard)
    except LabFailure as e:
        print(f"LAB FAILURE: {e}", file=sys.stderr)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        fail_path = OUTPUT_DIR / f"failure_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        fail_path.write_text(json.dumps({"error": str(e), "attempts": e.attempts},
                                        indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Failure log saved to {fail_path}", file=sys.stderr)
        sys.exit(1)

    concept["lab"]["vram_models_after"] = ollama.loaded_models()
    pretty = json.dumps(concept, indent=2, ensure_ascii=False)
    print(pretty)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUTPUT_DIR / (
        f"concept_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json")
    out.write_text(pretty, encoding="utf-8")
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
