"""Runtime configuration. Loads .env; never prints or logs secret values."""
from __future__ import annotations

import os
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

MAX_VALIDATION_RETRIES = 2  # §3.9: re-run with the error, max 2 retries

DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "plotline.db"
PROMPTS_DIR = ROOT / "prompts"
LOG_DIR = DATA_DIR / "runs"

for _d in (DATA_DIR, UPLOAD_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)
