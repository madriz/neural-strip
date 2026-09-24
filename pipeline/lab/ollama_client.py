# -*- coding: utf-8 -*-
"""Minimal Ollama HTTP client for Neural Strip Lab."""

import json
import time

import requests

from config import OLLAMA_TIMEOUT, OLLAMA_URL, TEXT_NUM_CTX


class OllamaError(RuntimeError):
    pass


def chat(model: str, system: str, user: str, *, schema: dict | None = None,
         temperature: float = 0.8, num_ctx: int = TEXT_NUM_CTX,
         keep_alive: str | int = "5m", think: bool = False) -> dict:
    """
    One non-streaming chat call. Returns
    {"content": str, "seconds": float, "prompt_tokens": int, "output_tokens": int}.

    With `schema`, Ollama constrains decoding to that JSON schema.
    Thinking is off unless think=True; reasoning text is never returned.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "keep_alive": keep_alive,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    if schema is not None:
        body["format"] = schema

    t0 = time.perf_counter()
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=OLLAMA_TIMEOUT)
    except requests.RequestException as e:
        raise OllamaError(f"Ollama unreachable at {OLLAMA_URL}: {e}") from e
    seconds = round(time.perf_counter() - t0, 2)
    if r.status_code != 200:
        raise OllamaError(f"Ollama {r.status_code}: {r.text[:300]}")
    data = r.json()

    prompt_tokens = data.get("prompt_eval_count", 0) or 0
    # Ollama silently drops the start of a prompt that overflows num_ctx.
    # Treat a prompt that fills the window as a hard error, not a quiet truncation.
    if prompt_tokens >= num_ctx - 16:
        raise OllamaError(
            f"Prompt filled the context window ({prompt_tokens} of {num_ctx} tokens); "
            f"raise NS_TEXT_NUM_CTX"
        )
    return {
        "content": data.get("message", {}).get("content", ""),
        "seconds": seconds,
        "prompt_tokens": prompt_tokens,
        "output_tokens": data.get("eval_count", 0) or 0,
        "thinking_chars": len(data.get("message", {}).get("thinking", "") or ""),
    }


def chat_json(model: str, system: str, user: str, schema: dict, **kw) -> tuple[dict, dict]:
    """Structured chat call. Returns (parsed_json, call_stats)."""
    res = chat(model, system, user, schema=schema, **kw)
    try:
        parsed = json.loads(res["content"])
    except json.JSONDecodeError as e:
        raise OllamaError(f"Model returned invalid JSON: {e}") from e
    stats = {k: v for k, v in res.items() if k != "content"}
    return parsed, stats


def unload(model: str) -> None:
    """Free the model from VRAM immediately (keep_alive 0). Never raises."""
    try:
        requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=60,
        )
    except requests.RequestException:
        pass


def loaded_models() -> list[str]:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/ps", timeout=10)
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []
