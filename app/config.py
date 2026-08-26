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


def _stage_model(stage: str, fallback: str) -> str:
    """Per-stage model override, defaulting to the broad slot.

    Six agents used to share PLANNER_MODEL, which meant paying planner rates to
    lint a shot board. Each stage now has its own env key and each one DEFAULTS
    to the slot it already used, so nothing moves unless it is set — this is a
    new dial, not a new behaviour.

    Deliberately NOT exposed in the product UI: the settings gear is
    honestly-inert and `test_settings_icon_is_disabled_and_hides_no_live_capability`
    sweeps the whole API for anything that names a model. This is an operator
    knob in `.env`, which is a different thing from a user-facing choice.
    """
    return os.environ.get(f"PLOTLINE_MODEL_{stage.upper()}", fallback)


# The stages that call an LLM, each independently switchable. Cheap stages are
# mechanical (lint a board, name four canon views); expensive ones are the ones
# that argue (options, the reviewer).
STAGE_MODELS = {
    "intake": _stage_model("intake", INTAKE_MODEL),
    "brief": _stage_model("brief", PLANNER_MODEL),
    "options": _stage_model("options", PLANNER_MODEL),
    "detail": _stage_model("detail", PLANNER_MODEL),
    "script": _stage_model("script", PLANNER_MODEL),
    "board": _stage_model("board", PLANNER_MODEL),
    "canon": _stage_model("canon", PLANNER_MODEL),
    "council": _stage_model("council", FEEDBACK_MODEL),
}

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

# Anthropic's NATIVE server-side web_search tool. No second vendor and no
# extra key — it bills to the same ANTHROPIC_API_KEY. Opt-in per agent (only
# intake asks for it today), because enrichment is an INPUT-gathering job: the
# planner and the council still argue from the retrieval corpus alone, so a web
# result can never quietly become the evidence behind a claim.
WEB_SEARCH = _truthy("PLOTLINE_WEB_SEARCH", "1")
WEB_SEARCH_MAX_USES = int(os.environ.get("PLOTLINE_WEB_SEARCH_MAX_USES", "3"))

MAX_VALIDATION_RETRIES = 2  # §3.9: re-run with the error, max 2 retries

# Output cap for one agent call. This is a CAP, not spend — you pay only for
# tokens actually generated. It must be generous because extended thinking
# tokens are billed against the SAME budget as the answer: a chair that thinks
# hard and then emits a large Feedback object can be cut off mid-JSON, which
# surfaces as a bogus "Expecting ',' delimiter" instead of an honest overflow.
MAX_OUTPUT_TOKENS = int(os.environ.get("PLOTLINE_MAX_OUTPUT_TOKENS", "32000"))

# --- Creative Studio media --------------------------------------------------
# PixelBin is the primary provider (owner decision 2026-08-26); fal.ai stays
# wired as the FALLBACK, so a PixelBin outage degrades instead of stopping the
# flow. MEDIA_PROVIDER names the one that is tried FIRST; the other is only
# reached if the first raises. Set to "fal" to invert, "pixelbin_only" /
# "fal_only" to disable the fallback entirely.
MEDIA_PROVIDER = os.environ.get("PLOTLINE_MEDIA_PROVIDER", "pixelbin").strip().lower()
PIXELBIN_API_TOKEN = os.environ.get("PIXELBIN_API_TOKEN", "")
PIXELBIN_CLOUD_NAME = os.environ.get("PIXELBIN_CLOUD_NAME", "")
PIXELBIN_DOMAIN = os.environ.get("PIXELBIN_DOMAIN", "https://api.pixelbin.io")
# PixelBin prediction names are "<plugin>_<operation>" and are a DIFFERENT
# namespace from fal's model slugs — the two maps are deliberately separate so
# a fallback never silently sends a fal slug to PixelBin or vice versa.
# Names verified against the live catalogue on 2026-08-26 (97 predictions).
# Veo is `veo31_*`, NOT `veo3_*` — the latter does not exist and would 404 at
# generation time, i.e. after the user had already approved a cost gate.
PIXELBIN_MODELS = {
    "image_draft": os.environ.get("PLOTLINE_PB_IMAGE_DRAFT", "nanoBanana_generate"),
    "image_final": os.environ.get("PLOTLINE_PB_IMAGE_FINAL", "nanoBanana2_generate"),
    "image_pro": os.environ.get("PLOTLINE_PB_IMAGE_PRO", "nanoBananaPro_generate"),
    "video": os.environ.get("PLOTLINE_PB_VIDEO", "veo31_generate"),
}
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

# v3 §5 lint B3 — how many reference images one call to each model can carry.
# Per-model and in CONFIG, never in a prompt: a silently dropped product
# reference is exactly how label and geometry drift enter a campaign, so the
# board has to force an explicit drop-or-split against a real number.
MEDIA_REF_SLOTS = {
    "image_draft": int(os.environ.get("PLOTLINE_REF_SLOTS_IMAGE_DRAFT", "3")),
    "image_final": int(os.environ.get("PLOTLINE_REF_SLOTS_IMAGE_FINAL", "4")),
    "image_pro": int(os.environ.get("PLOTLINE_REF_SLOTS_IMAGE_PRO", "6")),
    "video": int(os.environ.get("PLOTLINE_REF_SLOTS_VIDEO", "2")),
}
MEDIA_REF_SLOTS_DEFAULT = int(os.environ.get("PLOTLINE_REF_SLOTS_DEFAULT", "2"))


def media_key(kind: str, tier: str = "final") -> str:
    """The ONE key a (kind, tier) pair resolves to.

    MEDIA_MODELS, PIXELBIN_MODELS, MEDIA_COST_USD and MEDIA_REF_SLOTS are all
    keyed this way, and until now the mapping was written out by hand in three
    of the four call sites. Four tables that have to agree, kept in step by
    copy-paste, is three chances to drift — and the drift that matters here is
    silent: an unknown key falls back to a default that reads like a decision.
    """
    if kind != "image":
        return kind  # "video" | "audio"
    return {"draft": "image_draft", "pro": "image_pro"}.get(tier, "image_final")


def ref_slots(kind: str, tier: str = "final") -> int:
    """How many reference images ONE call to this model can carry (v3 §5, B3).

    The board's B3 lint polices shots against these numbers, so the client has
    to send against the same numbers or the lint is enforcing a capacity nobody
    honours — which is exactly the state this function was written to end. A
    missing key falls back to the conservative default, never to "unlimited":
    over-sending is a 400 at spend time, under-sending is a visible drop.
    """
    return MEDIA_REF_SLOTS.get(media_key(kind, tier), MEDIA_REF_SLOTS_DEFAULT)

DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "plotline.db"
PROMPTS_DIR = ROOT / "prompts"
LOG_DIR = DATA_DIR / "runs"

ASSET_DIR = DATA_DIR / "assets"

for _d in (DATA_DIR, UPLOAD_DIR, LOG_DIR, ASSET_DIR):
    _d.mkdir(parents=True, exist_ok=True)
