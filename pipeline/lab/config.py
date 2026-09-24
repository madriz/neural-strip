# -*- coding: utf-8 -*-
"""
Neural Strip Lab: shared configuration.

Every model name and endpoint can be overridden with an environment
variable so models can be swapped without code changes.
"""

import os
import subprocess
import sys
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

LAB_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = LAB_DIR.parent
REPO_ROOT = PIPELINE_DIR.parent

OUTPUT_DIR = LAB_DIR / "output"          # gitignored scratch output
LOGS_DIR = LAB_DIR / "logs"              # gitignored run logs
WORKFLOWS_DIR = LAB_DIR / "workflows"    # saved ComfyUI workflow JSON

SITE_LAB_DIR = REPO_ROOT / "website" / "lab"
SITE_LAB_JSON = SITE_LAB_DIR / "cartoons.json"
SITE_LAB_IMAGES = SITE_LAB_DIR / "images"
MAIN_CARTOONS_JSON = REPO_ROOT / "website" / "cartoons.json"

# Credentials live outside the repo. Values are read on demand, never printed.
CREDENTIALS_FILE = Path(
    os.environ.get("NS_CREDENTIALS_FILE", r"C:\Users\rodri\claude-projects\neural-strip-tokens.txt")
)

# Make the cloud pipeline importable (generate.py is reused, never modified).
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

# ── Local text model (Ollama) ────────────────────────────────────────────────

OLLAMA_URL = os.environ.get("NS_OLLAMA_URL", "http://127.0.0.1:11434")
TEXT_MODEL = os.environ.get("NS_TEXT_MODEL", "qwen3:8b")
JUDGE_MODEL = os.environ.get("NS_JUDGE_MODEL", TEXT_MODEL)
GUARD_MODEL = os.environ.get("NS_GUARD_MODEL", "llama-guard3:1b")
TEXT_NUM_CTX = int(os.environ.get("NS_TEXT_NUM_CTX", "12288"))
TEXT_TEMPERATURE = float(os.environ.get("NS_TEXT_TEMPERATURE", "0.8"))
OLLAMA_TIMEOUT = int(os.environ.get("NS_OLLAMA_TIMEOUT", "600"))

MAX_ATTEMPTS = 3

# Writer settings. Content, copy and format rules live in the shared system
# prompt in generate.py, so both pipelines run the same rules.
# NS_LAB_THINK: let the writer reason before answering (on by default).
# NS_LAB_BEST_OF: drafts per attempt; the model ranks them, the judge checks the top one.
LAB_THINK = os.environ.get("NS_LAB_THINK", "1") == "1"
LAB_BEST_OF = int(os.environ.get("NS_LAB_BEST_OF", "4"))

# ── Local image model (ComfyUI) ──────────────────────────────────────────────

COMFY_URL = os.environ.get("NS_COMFY_URL", "http://127.0.0.1:8188")
COMFY_DIR = Path(os.environ.get("NS_COMFY_DIR", r"C:\AI\ComfyUI"))


def hardware_info() -> dict:
    """Best-effort description of this PC for the /lab intro. Never raises."""
    info = {"cpu": "", "gpu": "", "ram_gb": None, "os": ""}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().splitlines()
        if out:
            name, mem = [s.strip() for s in out[0].split(",", 1)]
            info["gpu"] = f"{name} ({round(int(mem.split()[0]) / 1024)} GB)"
    except Exception:
        pass
    try:
        ps = (
            "$c=(Get-CimInstance Win32_Processor).Name;"
            "$r=[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB);"
            "$o=(Get-CimInstance Win32_OperatingSystem).Caption;"
            "Write-Output \"$c|$r|$o\""
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        cpu, ram, os_name = out.split("|")
        info.update(cpu=cpu.strip(), ram_gb=int(ram), os=os_name.replace("Microsoft ", "").strip())
    except Exception:
        pass
    return info
