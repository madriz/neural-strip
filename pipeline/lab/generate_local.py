#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neural Strip Lab: local cartoon concept generator.

Same feeds and dedup as the cloud pipeline (imported from pipeline/generate.py).
System prompt, content rules, copy rules and judge prompt come from
pipeline/lab/rules.py, the shared source both pipelines will use.
The model runs locally through Ollama with JSON-schema constrained output.

Each attempt is best-of-N:
  1. one call writes N drafts for one story (thinking on by default)
  2. drafts that break the deterministic rules are dropped
       (cloud keyword filter, copy rules, one-liner format, emoji)
  3. the model ranks the remaining drafts with a short reason
  4. the LLM judge checks the top draft only; if it fails, draft #2
  5. optional Llama Guard on the judged draft (--guard)
Up to 3 attempts, each carrying the previous failure reason.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ftfy

import config  # noqa: F401  (puts pipeline/ on sys.path)
from config import (GUARD_MODEL, JUDGE_MODEL, LAB_BEST_OF, LAB_THINK, MAX_ATTEMPTS,
                    OUTPUT_DIR, SITE_LAB_JSON, TEXT_MODEL, TEXT_TEMPERATURE)
import generate as cloud  # the cloud pipeline, reused read-only
import ollama_client as ollama
import rules

# ── Structured output schemas ────────────────────────────────────────────────

DRAFT_FIELDS = ("angle", "scene", "speaker", "setup", "punchline", "instagram_caption", "image_prompt")


def batch_schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "source_url": {"type": "string"},
            "drafts": {
                "type": "array", "minItems": n, "maxItems": n,
                "items": {"type": "object",
                          "properties": {f: {"type": "string"} for f in DRAFT_FIELDS},
                          "required": list(DRAFT_FIELDS)},
            },
        },
        "required": ["headline", "source_url", "drafts"],
    }


RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "ranking": {"type": "array", "items": {"type": "integer"}},
        "reason": {"type": "string"},
    },
    "required": ["ranking", "reason"],
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


def best_of_block(n: int) -> str:
    """Appended to the cloud system prompt: same rules, N drafts instead of one."""
    return (
        f"DRAFTS: Pick ONE story, then write {n} clearly different drafts for it. "
        f'Output {{"headline": ..., "source_url": ..., "drafts": [{n} objects with angle, scene, '
        f"speaker, setup, punchline, instagram_caption, image_prompt]}}. Every draft follows every "
        f"rule above, including the WRITING ORDER. "
        f"Vary the angle between drafts, not just the wording."
    )


RANK_SYSTEM = """\
You are the cartoon editor of a dry, observational single-panel humor feature about the AI industry.
Rank the drafts from funniest to least funny. Prefer a punchline that is a real joke with the twist
in its last few words, reads as one clean line, and needs no explanation. Penalize punchlines that
restate the headline. Return "ranking" as draft numbers, best first, and a short reason for the top pick."""

# ── Deterministic checks ─────────────────────────────────────────────────────

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]"
)


class LabFailure(RuntimeError):
    """Raised when all attempts fail. Carries the per-attempt log."""

    def __init__(self, message: str, attempts: list[dict]):
        super().__init__(message)
        self.attempts = attempts


def deterministic_check(concept: dict) -> str:
    """Cloud keyword filter + cloud copy and format rules + emoji. '' when clean."""
    if not cloud.brand_safety_check(concept):
        return "cloud keyword filter flagged the draft"
    reason = rules.copy_rules_check(concept)
    if reason:
        return reason
    for f in rules.COPY_FIELDS:
        if EMOJI_RE.search(str(concept.get(f, ""))):
            return f"emoji in {f}"
    return ""


# ── Model calls ──────────────────────────────────────────────────────────────

def ctx_for(*texts: str, headroom: int = 6144) -> int:
    """Context window sized to the prompt: about 3.5 chars per token plus room for thinking and output."""
    est = sum(len(t) for t in texts) // 3 + headroom
    return max(8192, -(-est // 4096) * 4096)


def judge(concept: dict) -> tuple[dict, dict]:
    """Layer (b): the shared judge prompt, joke fields only."""
    verdict, stats = ollama.chat_json(
        JUDGE_MODEL, rules.JUDGE_SYSTEM,
        rules.JUDGE_USER_TEMPLATE.format(draft=rules.judge_draft_text(concept, cloud.IMAGE_PROMPT_PREFIX)),
        JUDGE_SCHEMA, temperature=0.0, num_ctx=4096,
    )
    verdict["verdict"] = "pass" if verdict.get("verdict") == "pass" else "fail"
    return verdict, stats


def guard(concept: dict) -> tuple[dict, dict]:
    """Layer (c): Llama Guard on the joke fields."""
    text = "\n".join(str(concept.get(f, "")) for f in rules.JUDGE_FIELDS)
    res = ollama.chat(GUARD_MODEL, "", text, temperature=0.0, num_ctx=4096)
    out = res["content"].strip().lower()
    safe = out.startswith("safe")
    stats = {k: v for k, v in res.items() if k != "content"}
    return {"verdict": "pass" if safe else "fail",
            "categories": re.findall(r"s\d+", out) if not safe else []}, stats


def rank(drafts: list[dict], headline: str) -> tuple[list[int], str, dict]:
    """Model ranks drafts. Returns (0-based order, reason, stats). Missing numbers go last."""
    listing = "\n\n".join(
        f"Draft {i}:\n  angle: {d['angle']}\n  scene: {d['image_scene']}\n  speaker: {d['speaker']}\n"
        f"  caption: {d['punchline']}"
        for i, d in enumerate(drafts, 1))
    res, stats = ollama.chat_json(
        TEXT_MODEL, RANK_SYSTEM, f"Story: {headline}\n\n{listing}", RANK_SCHEMA,
        temperature=0.0, num_ctx=ctx_for(listing, headroom=2048))
    order = []
    for x in res.get("ranking", []):
        if isinstance(x, int) and 1 <= x <= len(drafts) and (x - 1) not in order:
            order.append(x - 1)
    order += [i for i in range(len(drafts)) if i not in order]
    return order, res.get("reason", ""), stats


def _clean(v) -> str:
    v = ftfy.fix_text(str(v or "").strip())
    for bad, good in cloud.MOJIBAKE_FIXES.items():
        v = v.replace(bad, good)
    return v


def _to_concept(headline: str, source_url: str, d: dict) -> dict:
    """Mirror the cloud clean-up: ftfy, mojibake table, locked style prefix."""
    c = {"headline": _clean(headline), "source_url": _clean(source_url)}
    c.update({f: _clean(d.get(f)) for f in DRAFT_FIELDS})
    c["punchline"] = rules.strip_wrapping_quotes(c["punchline"])
    c["image_scene"] = c["image_prompt"]
    if c["image_prompt"]:
        c["image_prompt"] = cloud.IMAGE_PROMPT_PREFIX + c["image_prompt"]
    return c


# ── Generation loop ──────────────────────────────────────────────────────────

def generate_with_safety(system: str, user_prompt: str, *, use_guard: bool = False,
                         forced_story: dict | None = None, n: int = LAB_BEST_OF) -> dict:
    """Best-of-N generate, filter, rank, judge top 2, up to MAX_ATTEMPTS times."""
    attempts: list[dict] = []
    timings: dict[str, float] = {}
    guidance = ""
    schema = batch_schema(n)

    for a in range(1, MAX_ATTEMPTS + 1):
        rec: dict = {"attempt": a}
        attempts.append(rec)
        sys_prompt = system + "\n\n" + best_of_block(n) + (f"\n\n{guidance}" if guidance else "")

        # 1. N drafts in one call
        try:
            raw, stats = ollama.chat_json(TEXT_MODEL, sys_prompt, user_prompt, schema,
                                          temperature=TEXT_TEMPERATURE, think=LAB_THINK,
                                          num_ctx=ctx_for(sys_prompt, user_prompt))
        except ollama.OllamaError as e:
            rec.update(stage="generate", reason=str(e))
            guidance = "Previous attempt failed: output must be a single valid JSON object."
            continue
        timings[f"generate_attempt_{a}"] = stats["seconds"]
        rec["generate_tokens"] = {"prompt": stats["prompt_tokens"], "output": stats["output_tokens"]}

        headline, url = raw.get("headline", ""), raw.get("source_url", "")
        if forced_story:
            headline, url = forced_story["title"], forced_story.get("link", "")
        drafts = [_to_concept(headline, url, d) for d in raw.get("drafts", [])][:n]

        # 2. deterministic filter
        rec["drafts"] = []
        ok = []
        for d in drafts:
            why = deterministic_check(d)
            rec["drafts"].append({"punchline": d["punchline"], "setup": d["setup"], "rejected": why})
            if not why:
                ok.append(d)
        if not ok:
            reasons = "; ".join(sorted({x["rejected"] for x in rec["drafts"]}))
            rec.update(stage="deterministic", reason=f"all {len(drafts)} drafts rejected: {reasons}")
            guidance = (f"Previous attempt was rejected: {reasons}. Follow CAPTION FORMAT and "
                        f"CONTENT RULES exactly.")
            continue

        # 3. rank
        if len(ok) > 1:
            try:
                order, why_top, rstats = rank(ok, headline)
                timings[f"rank_attempt_{a}"] = rstats["seconds"]
            except ollama.OllamaError as e:
                order, why_top = list(range(len(ok))), f"ranking failed, kept draft order: {e}"
        else:
            order, why_top = [0], "only one draft passed the deterministic checks"
        rec["ranking"] = {"order": [ok[i]["punchline"] for i in order], "reason": why_top}

        # 4. judge top, fall back to #2
        chosen = verdict = gverdict = None
        rec["judged"] = []
        for pos, idx in enumerate(order[:2], 1):
            cand = ok[idx]
            try:
                v, jstats = judge(cand)
            except ollama.OllamaError as e:
                rec["judged"].append({"rank": pos, "error": str(e)})
                continue
            timings[f"judge_attempt_{a}_rank_{pos}"] = jstats["seconds"]
            rec["judged"].append({"rank": pos, "punchline": cand["punchline"], **v})
            if v["verdict"] != "pass":
                continue
            # 5. optional Llama Guard
            if use_guard:
                try:
                    g, gstats = guard(cand)
                except ollama.OllamaError as e:
                    rec["judged"][-1]["guard_error"] = str(e)
                    continue
                timings[f"guard_attempt_{a}_rank_{pos}"] = gstats["seconds"]
                rec["judged"][-1]["guard"] = g
                if g["verdict"] != "pass":
                    continue
                gverdict = g
            chosen, verdict = cand, v
            rec["chosen_rank"] = pos
            break

        if chosen is None:
            reasons = " | ".join(j.get("reason") or j.get("error") or "guard flagged" for j in rec["judged"])
            rec.update(stage="judge", reason=reasons)
            guidance = (f"Previous attempt was rejected by the content reviewer: {reasons}. "
                        f"Fix this in the new drafts.")
            continue

        rec["stage"] = "passed"
        chosen["lab"] = {
            "text_model": TEXT_MODEL,
            "judge_model": JUDGE_MODEL,
            "guard_model": GUARD_MODEL if use_guard else None,
            "think": LAB_THINK,
            "best_of": n,
            "attempts_used": a,
            "attempts": attempts,
            "rank_reason": why_top,
            "judge": verdict,
            "guard": gverdict,
            "timings_s": timings,
        }
        return chosen

    reasons = "; ".join(f"#{x['attempt']} {x.get('stage')}: {x.get('reason', '')}" for x in attempts)
    raise LabFailure(f"All {MAX_ATTEMPTS} attempts failed: {reasons}", attempts)


# ── Modes ────────────────────────────────────────────────────────────────────

def lab_recent_headlines(days: int = 7) -> list[str]:
    """Headlines the lab itself used recently (independent mode dedup)."""
    try:
        data = json.loads(SITE_LAB_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return sorted({
        (e.get("local") or {}).get("headline", "")
        for e in data.get("entries", [])
        if e.get("date", "") >= cutoff and e.get("kind") == "independent"
    } - {""})


def build_system_prompt(dedup: list[str]) -> str:
    """Shared system prompt plus the cloud dedup block, byte for byte."""
    system = rules.SYSTEM_PROMPT
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
        concept = generate_with_safety(rules.SYSTEM_PROMPT, user_prompt,
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


def write_failure(e: LabFailure) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"failure_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"error": str(e), "attempts": e.attempts}, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


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

    print(f"Neural Strip Lab | text={TEXT_MODEL} judge={JUDGE_MODEL} think={LAB_THINK} "
          f"best_of={LAB_BEST_OF}{' guard=' + GUARD_MODEL if args.guard else ''}")
    try:
        if args.headline:
            concept = run_for_story({"title": args.headline, "summary": args.summary,
                                     "link": args.url}, use_guard=args.guard)
        else:
            concept = run_independent(use_guard=args.guard)
    except LabFailure as e:
        print(f"LAB FAILURE: {e}", file=sys.stderr)
        print(f"Failure log saved to {write_failure(e)}", file=sys.stderr)
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
