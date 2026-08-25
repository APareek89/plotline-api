"""Runtime configuration. Loads .env; never prints or logs secret values."""
from __future__ import annotations

import os
from typing import Optional
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Corp TLS-intercepting proxy: Jupyter/uvicorn processes don't source
# .venv/bin/activate, so pin the CA bundle here before any outbound call.
_CA_BUNDLE = os.environ.get(
    "PLOTLINE_CA_BUNDLE",
    "/Users/anandpareek/Documents/SEO content Skill/scripts/system-ca-bundle.pem",
)
if Path(_CA_BUNDLE).exists():
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        os.environ.setdefault(var, _CA_BUNDLE)

def _truthy(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# --- services ---------------------------------------------------------------
API_HOST = os.environ.get("PLOTLINE_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("PLOTLINE_API_PORT", "8600"))
# Frozen interface (§7): Codex's plotline-rag serves this contract (contract
# default port 8787; locally it runs on 8788 while devrag holds 8787).
RAG_BASE_URL = os.environ.get("PLOTLINE_RAG_URL", "http://127.0.0.1:8787")
RAG_TIMEOUT = float(os.environ.get("PLOTLINE_RAG_TIMEOUT_SECONDS", "10"))
RAG_ENABLED = _truthy("PLOTLINE_RAG_ENABLED", "1")
# plotline-rag's corpus currently ships 10 dev-only tier="seed" chunks.
# Seed evidence must never read as official/expert guidance, so it is
# rejected unless explicitly allowed (local integration testing only).
ALLOW_SEED_EVIDENCE = _truthy("PLOTLINE_ALLOW_SEED_EVIDENCE", "0")
# devrag keeps serving the corpora plotline-rag doesn't host yet
# (asset:/stat:/trend: sample fixtures). Empty = those corpora are absent.
AUX_RAG_URL = os.environ.get("PLOTLINE_AUX_RAG_URL", "")

# --- models (PRD §7: Sonnet = plan/critique/studio, Haiku = intake/ingest) ---
PLANNER_MODEL = os.environ.get("PLOTLINE_PLANNER_MODEL", "claude-sonnet-4-6")
FEEDBACK_MODEL = os.environ.get("PLOTLINE_FEEDBACK_MODEL", "claude-sonnet-4-6")
INTAKE_MODEL = os.environ.get("PLOTLINE_INTAKE_MODEL", "claude-haiku-4-5")
INTAKE_VISION_MODEL = os.environ.get("PLOTLINE_INTAKE_VISION_MODEL", "claude-sonnet-4-6")

# MOCK_LLM=1 → deterministic agent outputs built from fixtures; the full
# orchestrator + validators + retrieval still run. For UI dev and tests
# without an API key. Real mode needs ANTHROPIC_API_KEY in .env.
MOCK_LLM = os.environ.get("MOCK_LLM", "0") == "1"

# Real mode with no key is a deployment mistake, not a runtime surprise. Without
# this the first agent call dies deep inside the SDK and the user sees a generic
# 500 — the same class of dishonesty as parsing a truncated response.
LLM_KEY_PRESENT = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def llm_unavailable_reason() -> Optional[str]:
    """Why a real agent call cannot be made right now, or None if it can."""
    if MOCK_LLM:
        return None
    if not LLM_KEY_PRESENT:
        return ("MOCK_LLM=0 but ANTHROPIC_API_KEY is not set on this deployment — "
                "the agents cannot run. Set the key, or set MOCK_LLM=1 to use "
                "deterministic sample output.")
    return None

MAX_VALIDATION_RETRIES = 2  # §3.9: re-run with the error, max 2 retries

# Output cap for one agent call. This is a CAP, not spend — you pay only for
# tokens actually generated. It must be generous because extended thinking
# tokens are billed against the SAME budget as the answer: a chair that thinks
# hard and then emits a large Feedback object can be cut off mid-JSON, which
# surfaces as a bogus "Expecting ',' delimiter" instead of an honest overflow.
MAX_OUTPUT_TOKENS = int(os.environ.get("PLOTLINE_MAX_OUTPUT_TOKENS", "32000"))

# --- Creative Studio media (Addendum-02; fal.ai per v1 §07) -----------------
FAL_KEY = os.environ.get("FAL_KEY", "")
# MOCK_MEDIA=1 → deterministic placeholder assets, zero spend; the full flow
# (prompt artifacts, cost lines, per-asset accept/reroll, Post Card) still runs.
MOCK_MEDIA = _truthy("MOCK_MEDIA", "1")
MEDIA_MODELS = {
    "image_draft": os.environ.get("PLOTLINE_MODEL_IMAGE_DRAFT", "fal-ai/nano-banana"),
    "image_final": os.environ.get("PLOTLINE_MODEL_IMAGE_FINAL", "fal-ai/nano-banana-2"),
    "image_pro": os.environ.get("PLOTLINE_MODEL_IMAGE_PRO", "fal-ai/nano-banana-pro"),
    "video": os.environ.get("PLOTLINE_MODEL_VIDEO", "fal-ai/veo3.1/fast/image-to-video"),
    "tts_draft": os.environ.get("PLOTLINE_MODEL_TTS_DRAFT", "fal-ai/kokoro/american-english"),
    "tts_final": os.environ.get("PLOTLINE_MODEL_TTS_FINAL", "fal-ai/minimax/speech-02-hd"),
}
# USD estimates shown before every generate (1 credit = $0.10). Env-overridable;
# these are ESTIMATES — the honest number is whatever fal bills.
MEDIA_COST_USD = {
    "image_draft": float(os.environ.get("PLOTLINE_COST_IMAGE_DRAFT", "0.04")),
    "image_final": float(os.environ.get("PLOTLINE_COST_IMAGE_FINAL", "0.08")),
    "image_pro": float(os.environ.get("PLOTLINE_COST_IMAGE_PRO", "0.15")),
    "video_per_s": float(os.environ.get("PLOTLINE_COST_VIDEO_PER_S", "0.10")),
    "tts_draft_per_1k": float(os.environ.get("PLOTLINE_COST_TTS_DRAFT", "0.02")),
    "tts_final_per_1k": float(os.environ.get("PLOTLINE_COST_TTS_FINAL", "0.10")),
}

DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "plotline.db"
PROMPTS_DIR = ROOT / "prompts"
LOG_DIR = DATA_DIR / "runs"

ASSET_DIR = DATA_DIR / "assets"

for _d in (DATA_DIR, UPLOAD_DIR, LOG_DIR, ASSET_DIR):
    _d.mkdir(parents=True, exist_ok=True)
