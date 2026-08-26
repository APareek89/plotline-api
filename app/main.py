"""plotline-api — orchestrator + agents (§7 build handoff: Claude Code lane).

Consumes the plotline-rag HTTP contract; does not embed, index, or query
vectors itself. Run: uvicorn app.main:app --port 8600
"""
from __future__ import annotations

import asyncio
import os
import json
import re
import shutil
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from app import brand_extract, campaign, config, store, threadkit
from app import seats as seats_mod
from app.agents.runner import AgentHardFail
from app.rag_client import RagUnavailable, rag
from app.schemas import CreatorContext, UserEvent

app = FastAPI(title="plotline-api", version="0.1.0")


@app.on_event("startup")
def _rag_startup_check() -> None:
    """Log the RAG manifest at boot. Non-fatal here — evidence-requiring flows
    re-check via rag.ensure_ready() and fail loudly if the service is down."""
    import logging

    if not config.RAG_ENABLED:
        logging.getLogger("plotline.rag").warning(
            "retrieval DISABLED (PLOTLINE_RAG_ENABLED=0) — plan generation will refuse to run"
        )
        return
    try:
        rag.ensure_ready()
    except RagUnavailable as exc:
        logging.getLogger("plotline.rag").warning("plotline-rag not reachable at startup: %s", exc)

_extra_origins = [o.strip() for o in os.environ.get("PLOTLINE_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3100", "http://127.0.0.1:3100", *_extra_origins],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    rag_status: dict[str, Any]
    try:
        rag_status = rag.health()
    except RagUnavailable as exc:
        rag_status = {"error": str(exc)}
    rag_status["enabled"] = config.RAG_ENABLED
    rag_status["allow_seed_evidence"] = config.ALLOW_SEED_EVIDENCE
    return {
        "service": "plotline-api",
        "mock_llm": config.MOCK_LLM,
        "media_mock": config.MOCK_MEDIA,
        # real mode with no key is a broken deployment — say so here rather
        # than letting the first campaign discover it
        "llm_unavailable": config.llm_unavailable_reason(),
        "models": {
            "planner": config.PLANNER_MODEL,
            "feedback": config.FEEDBACK_MODEL,
            "intake": config.INTAKE_MODEL,
        },
        "rag": rag_status,
    }


# ----------------------------------------------------------------- profile --


class ProfileBody(BaseModel):
    niche: Optional[str] = None
    tone_rules: list[str] = Field(default_factory=list)
    banned_topics: list[str] = Field(default_factory=list)
    capacity: Optional[str] = None
    style_prefs: list[str] = Field(default_factory=list)


@app.get("/api/profile")
def get_profile() -> dict[str, Any]:
    return store.get_profile()


@app.put("/api/profile")
def put_profile(body: ProfileBody) -> dict[str, Any]:
    store.save_profile(body.model_dump())
    return store.get_profile()


# ----------------------------------------------------------------- uploads --


@app.post("/api/uploads")
async def upload_file(
    file: UploadFile = File(...),
    kind: str = Form("other"),
    series_id: Optional[str] = Form(None),
) -> dict[str, Any]:
    safe_name = (file.filename or "upload").replace("/", "_")
    dest = config.UPLOAD_DIR / f"{store.new_id('f')}_{safe_name}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    upload_id = store.add_upload(safe_name, kind, str(dest), file.content_type, series_id)
    return {"id": upload_id, "filename": safe_name, "kind": kind}


@app.get("/api/uploads")
def list_uploads() -> list[dict[str, Any]]:
    return store.list_uploads()


# ------------------------------------------------------------------ series --


class SeriesCreateBody(BaseModel):
    form: dict[str, Any]
    upload_ids: list[str] = Field(default_factory=list)


class ContextUpdateBody(BaseModel):
    form: dict[str, Any]
    upload_ids: list[str] = Field(default_factory=list)


# -------------------------------------------------------------- inspiration --


# -------------------------------------------- Addendum-01 §01/§02: threads --


@app.get("/api/threads/{thread_id}")
def get_thread(thread_id: str, after_seq: int = 0) -> dict[str, Any]:
    thread = store.get_thread(thread_id)
    if not thread:
        raise HTTPException(404, "thread not found")
    series = store.get_series(thread["series_id"])
    return {
        **thread,
        "series_name": series["name"] if series else "",
        "working": threadkit.working_step(thread_id),  # labeled step, never a bare spinner
        "messages": store.get_messages(thread_id, after_seq=after_seq),
        "concept_states": store.get_concept_states(thread["series_id"]),
    }


@app.post("/api/threads/{thread_id}/events")
def post_event(thread_id: str, body: UserEvent) -> dict[str, Any]:
    if body.thread_id != thread_id:
        raise HTTPException(422, "thread_id mismatch")
    thread = store.get_thread(thread_id)
    if not thread:
        raise HTTPException(404, "thread not found")
    if thread["kind"] != "campaign":
        # Content Studio and Creative Studio threads are gone. An old row can
        # still be in a dev database, so say what happened rather than 500.
        raise HTTPException(410, f"thread kind {thread['kind']!r} is no longer supported")
    try:
        campaign.handle_event(body)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.get("/api/threads/{thread_id}/artifacts/{artifact_id}/activity")
def artifact_activity(thread_id: str, artifact_id: str) -> list[dict[str, Any]]:
    return store.get_artifact_activity(thread_id, artifact_id)


# ----------------------------- Addendum-02: Creative Studio + Post Cards ----


class ProduceBody(BaseModel):
    option: str = "A"


@app.get("/api/assets/{asset_id}/file")
def asset_file(asset_id: str):
    from fastapi.responses import FileResponse

    asset = store.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    media_types = {"svg": "image/svg+xml", "png": "image/png", "mp4": "video/mp4",
                   "wav": "audio/wav", "mp3": "audio/mpeg"}
    ext = asset["path"].rsplit(".", 1)[-1]
    return FileResponse(asset["path"], media_type=media_types.get(ext, "application/octet-stream"))


@app.get("/api/threads/{thread_id}/generation-log")
def generation_log(thread_id: str) -> list[dict[str, Any]]:
    return store.get_generation_log(thread_id)


class PromptEditBody(BaseModel):
    prompt_text: str


@app.post("/api/threads/{thread_id}/prompts/{slot}")
def edit_prompt(thread_id: str, slot: str, body: PromptEditBody) -> dict[str, Any]:
    """§08 rule 7: the user's edited prompt is used VERBATIM downstream."""
    ws = threadkit._pending(thread_id)
    if slot not in ws.get("prompts", {}):
        raise HTTPException(404, f"no pending prompt for slot {slot}")
    ws["prompts"][slot] = body.prompt_text
    store.log_generation(thread_id, None, "edit_prompt", prompt=body.prompt_text)
    store.log_artifact_activity(thread_id, f"prompt_{slot}", "refined", "user edited prompt")
    return {"slot": slot, "prompt_text": body.prompt_text}


# --------------------------- Addendum-03: Marketing Studio — campaigns ------



def _campaign_or_404(campaign_id: str) -> dict[str, Any]:
    series = store.get_series(campaign_id)  # a campaign IS a series row
    if not series:
        raise HTTPException(404, "campaign not found")
    return series


class CampaignCreateBody(BaseModel):
    name: str


@app.post("/api/campaigns")
def create_campaign(body: CampaignCreateBody) -> dict[str, Any]:
    """Step 0: the blank landing's only block. Name is the Campaigns-tab
    handle and the thread numbering prefix, so it must be unique."""
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Naming is required: give the campaign a name")
    if any(row["name"].strip().lower() == name.lower() for row in list_campaigns()):
        raise HTTPException(422, f"A campaign named '{name}' already exists — pick another name")
    return campaign.start_campaign(name)


@app.get("/api/campaigns")
def list_campaigns() -> list[dict[str, Any]]:
    """My Campaigns rows. Campaign series are the ones carrying a campaign
    thread; thread_id is the latest one, which the Open CTA lands on."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for thread in store.list_threads(kind="campaign"):  # newest first
        series_id = thread["series_id"]
        if series_id in seen:
            continue
        seen.add(series_id)
        series = store.get_series(series_id)
        if not series:
            continue
        context = series["context"]
        rows.append({
            "id": series_id,
            "name": series["name"],
            "objective": (context.get("campaign") or {}).get("objective"),
            "status": series["status"],
            "creative_count": len(store.list_ad_cards(series_id)),
            "spend_credits": round(store.campaign_spend(series_id) / threadkit.CREDIT_USD, 1),
            "thread_id": thread["id"],
        })
    return rows


@app.get("/api/campaigns/{campaign_id}")
def get_campaign(campaign_id: str) -> dict[str, Any]:
    series = _campaign_or_404(campaign_id)
    return {
        "id": campaign_id,
        "name": series["name"],
        "context": series["context"],
        "status": series["status"],
        "context_filled": campaign.context_filled(series["context"]),
        "threads": store.get_series_threads(campaign_id),
        "ad_cards": store.list_ad_cards(campaign_id),
    }


@app.delete("/api/campaigns/{campaign_id}")
def delete_campaign(campaign_id: str) -> dict[str, Any]:
    """Remove a campaign and everything hanging off it.

    The grid needs a working per-card action, and until now there was none —
    which is why a dev database accumulated 31 QA campaigns with no way to
    clear them. Deletion is genuinely destructive, so it is a DELETE with no
    'archive' fallback pretending to be one: the row is gone, and the response
    says what went with it rather than a bare ok.
    """
    _campaign_or_404(campaign_id)
    removed = store.delete_series(campaign_id)
    return {"deleted": campaign_id, **removed}


class BrandFetchBody(BaseModel):
    url: str


@app.post("/api/campaigns/{campaign_id}/brand/fetch")
def brand_fetch(campaign_id: str, body: BrandFetchBody) -> dict[str, Any]:
    """Fetch from URL — a SYSTEM extractor pipeline, never an agent tool.
    Returns candidates only: nothing is saved until the user confirms the card."""
    _campaign_or_404(campaign_id)
    url = body.url.strip()
    if not url:
        raise HTTPException(422, "Brand URL is required to fetch")
    return brand_extract.extract(url)


def _pdf_text(raw: bytes) -> str:
    """Best-effort PDF text with the stdlib only (no PDF library in the venv):
    inflate the content streams and take the string operands. Deliberately
    crude — enough for a typed policy, empty for a scanned one."""
    import zlib

    chunks: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        blob = match.group(1)
        try:
            blob = zlib.decompress(blob)
        except zlib.error:
            if b"Tj" not in blob and b"TJ" not in blob:
                continue  # binary image/font stream, not page text
        chunks += re.findall(rb"\((?:\\.|[^\\()])*\)", blob)
    text = b" ".join(c[1:-1] for c in chunks).decode("latin-1", "replace")
    return re.sub(r"[ \t]+", " ", re.sub(r"\\([()\\])", r"\1", text)).strip()


def _policy_text(upload_id: Optional[str]) -> tuple[str, list[str]]:
    """Text of the uploaded Brand Policy Document. Unreadable file → empty
    text plus an honest note; never invented content."""
    if not upload_id:
        return "", ["no Brand Policy Document uploaded — candidates come from the product description only"]
    rows = store.get_uploads([upload_id])
    if not rows:
        return "", [f"policy upload {upload_id} not found"]
    upload, path = rows[0], Path(rows[0]["path"])
    if not path.exists():
        return "", [f"policy file missing on disk ({upload['filename']})"]
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".markdown"):
        return path.read_text(encoding="utf-8", errors="replace"), []
    if suffix == ".pdf":
        text = _pdf_text(path.read_bytes())
        if not text:
            return "", [f"couldn't read text out of {upload['filename']} "
                        "(scanned or encoded PDF) — add the claims by hand"]
        return text, [f"{upload['filename']} read with a best-effort PDF parser — check the candidates"]
    return "", [f"unsupported policy format '{suffix or 'unknown'}' — upload .txt, .md or .pdf"]


@app.post("/api/campaigns/{campaign_id}/claims/extract")
def claims_extract(campaign_id: str) -> dict[str, Any]:
    """Compliance without a new form field: CANDIDATE claims + banned words
    from the policy doc and product description. The user one-tap confirms
    them in the Brand card — the confirmed list is the source of truth."""
    series = _campaign_or_404(campaign_id)
    context = series["context"]
    brand, product = context.get("brand") or {}, context.get("product") or {}
    policy_text, notes = _policy_text(brand.get("policy_upload_id"))
    out = brand_extract.extract_claims(policy_text, product.get("description", ""))
    if not out["approved_claims"] and not out["banned_words"]:
        notes.append("nothing extractable — add claims and banned words yourself")
    return {**out, "notes": notes}


@app.post("/api/campaigns/{campaign_id}/start")
def start_campaign(campaign_id: str) -> dict[str, Any]:
    """Step 3 kick-off: all cards ✓ → rumination runs in the thread."""
    series = _campaign_or_404(campaign_id)
    blocked = config.llm_unavailable_reason()
    if blocked:
        raise HTTPException(503, blocked)
    missing = campaign.missing_blocks(series["context"])
    if missing:
        raise HTTPException(422, f"Campaign context incomplete — still needed: {', '.join(missing)}")
    try:
        campaign.begin_rumination(campaign_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


@app.get("/api/campaigns/{campaign_id}/settings")
def get_campaign_settings(campaign_id: str) -> dict[str, Any]:
    """Review policy + council roster for one campaign.

    Always answers, even for a campaign that has never been configured — the
    defaults ARE the product's behaviour, so an absent row is a full answer
    rather than a missing one.
    """
    _campaign_or_404(campaign_id)
    policy = campaign._policy_of(campaign_id)
    stored = store.get_campaign_settings(campaign_id)
    return {
        "review_policy": policy.model_dump(mode="json"),
        "seats": stored.get("seats") or [],
        "gateable_stages": campaign.GATEABLE_STAGES,
        "presets": sorted(campaign.POLICY_PRESETS),
        "available_seats": sorted(seats_mod.available()),
    }


@app.patch("/api/campaigns/{campaign_id}/settings")
def patch_campaign_settings(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Merge-patch. Sending one setting never clears the others.

    Validation happens HERE, on write, so a bad gate combination or an unknown
    seat is refused while the user is looking at it — rather than degrading
    silently in the middle of a paid run.
    """
    _campaign_or_404(campaign_id)
    patch: dict[str, Any] = {}

    if "preset" in body:
        try:
            patch["review_policy"] = campaign.policy_from_preset(
                body["preset"]).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    if "review_policy" in body:
        # merge onto what is already there, so PATCHing one count keeps the gates
        current = campaign._policy_of(campaign_id).model_dump(mode="json")
        incoming = body["review_policy"] or {}
        merged = {**current, **incoming}
        if "gates" in incoming:
            merged["gates"] = {**current["gates"], **(incoming["gates"] or {})}
        try:
            patch["review_policy"] = campaign.ReviewPolicy.model_validate(
                merged).model_dump(mode="json")
        except ValidationError as exc:
            raise HTTPException(422, _first_error(exc)) from exc

    if "seats" in body:
        wanted = body["seats"] or []
        if not isinstance(wanted, list):
            raise HTTPException(422, "seats must be a list of seat slugs")
        try:
            seats_mod.resolve(wanted)
        except seats_mod.SeatConfigError as exc:
            raise HTTPException(422, str(exc)) from exc
        patch["seats"] = wanted

    if not patch:
        raise HTTPException(422, "nothing to update — send preset, review_policy or seats")

    store.update_campaign_settings(campaign_id, patch)
    return get_campaign_settings(campaign_id)


def _first_error(exc: ValidationError) -> str:
    """A pydantic error dump is unreadable in a toast. Surface the message the
    hard-rule validators were written to say."""
    for err in exc.errors():
        msg = err.get("msg", "")
        return msg.removeprefix("Value error, ")
    return str(exc)


@app.get("/api/canon")
def list_canon(kind: Optional[str] = None) -> dict[str, Any]:
    """The workspace canon library. Global by design — a sheet belongs to the
    workspace, not to the campaign that happened to create it first."""
    return {"sheets": store.list_canon_sheets(kind)}


@app.get("/api/canon/{sheet_id}")
def get_canon(sheet_id: str) -> dict[str, Any]:
    sheet = store.get_canon_sheet(sheet_id)
    if not sheet:
        raise HTTPException(404, f"no canon sheet {sheet_id}")
    return sheet


@app.delete("/api/canon/{sheet_id}")
def delete_canon(sheet_id: str) -> dict[str, Any]:
    """Deleting a sheet a campaign still references does NOT retroactively break
    that campaign — its shots hold the asset ids, not the sheet. What it costs is
    the reuse: the next campaign re-renders."""
    if not store.delete_canon_sheet(sheet_id):
        raise HTTPException(404, f"no canon sheet {sheet_id}")
    return {"ok": True, "deleted": sheet_id}


@app.get("/api/templates")
def templates() -> list[dict[str, Any]]:
    """Step 4: static samples from samples/templates/manifest.json. Empty
    until the owner drops files in — the picker then offers Skip only."""
    return campaign.templates()


# ------------------------------------------------- Addendum-03: ad cards ----


@app.get("/api/ad-cards")
def ad_cards(campaign_id: Optional[str] = None) -> list[dict[str, Any]]:
    return store.list_ad_cards(campaign_id)


@app.get("/api/ad-cards/{card_id}")
def ad_card(card_id: str) -> dict[str, Any]:
    card = store.get_ad_card(card_id)
    if not card:
        raise HTTPException(404, "ad card not found")
    return card


@app.get("/api/ad-cards/{card_id}/bundle")
def ad_card_bundle(card_id: str):
    """Export bundle — zip of media + copy_<platform>.txt + meta.json."""
    import io
    import zipfile

    card = store.get_ad_card(card_id)
    if not card:
        raise HTTPException(404, "ad card not found")
    naming = re.sub(r"[^A-Za-z0-9_.-]+", "_", card.get("naming") or card_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for platform, text in (card.get("placements") or {}).items():
            zf.writestr(f"copy_{platform}.txt", text)
        zf.writestr("meta.json", json.dumps(card, indent=2, default=str))
        for m in card.get("media") or []:
            asset_id = m["url"].rstrip("/").split("/")[-2] if m["url"].endswith("/file") else None
            asset = store.get_asset(asset_id) if asset_id else None
            if asset:
                p = Path(asset["path"])
                if p.exists():
                    zf.writestr(f"media/{naming}_{m['params'].get('prompt_id', p.stem)}{p.suffix}", p.read_bytes())
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{card_id}.zip"'})


@app.post("/api/ad-cards/{card_id}/mark-live")
def mark_live(card_id: str) -> dict[str, Any]:
    """Live is a campaign-level fact too: the row in My Campaigns flips with
    the card, and Live campaigns start showing the results-paste nudge."""
    card = store.get_ad_card(card_id)
    if not card:
        raise HTTPException(404, "ad card not found")
    card["status"] = "live"
    store.save_ad_card(card)
    store.set_campaign_status(card["campaign_id"], "live")
    return card


# ------------------------------------------------------------------- runs ---


class RegenBody(BaseModel):
    feedback: str = ""


# ------------------------------------------------------ concept-level moves --


class ReorderBody(BaseModel):
    ordered_ids: list[str]


# -------------------------------------------------------------- performance --


class PerformanceBody(BaseModel):
    series_id: Optional[str] = None
    concept_id: Optional[str] = None
    platform: str
    views: Optional[int] = None
    retention_pct: Optional[float] = None
    saves: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    ctr_pct: Optional[float] = None
    notes: Optional[str] = None


@app.post("/api/performance")
def add_performance(body: PerformanceBody) -> dict[str, Any]:
    metrics = {k: v for k, v in body.model_dump().items() if k not in ("series_id", "concept_id", "platform") and v is not None}
    row_id = store.add_performance(body.series_id, body.concept_id, body.platform, metrics)
    return {"id": row_id}


@app.get("/api/performance")
def list_performance() -> list[dict[str, Any]]:
    return store.list_performance()


# --------------------------------------------------------- observability ----
# TEMPORARY debug surface. Returns the structured input and output of every
# agent node that ran for a thread, newest last, so a run can be inspected
# without tailing a JSONL file. Gated: it exposes full prompts and payloads, so
# it must never answer in a real deployment.


def _debug_enabled() -> bool:
    return config.MOCK_LLM or _truthy_env("PLOTLINE_DEBUG_OBSERVABILITY")


def _truthy_env(name: str) -> bool:
    import os
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _read_runs() -> list[dict[str, Any]]:
    path = config.LOG_DIR / "agent_runs.jsonl"
    if not path.exists():
        return []
    runs: list[dict[str, Any]] = []
    buf = ""
    for line in path.read_text(errors="replace").splitlines(True):
        buf += line
        try:
            row = json.loads(buf)
        except Exception:
            continue                       # a pretty-printed record spans lines
        buf = ""
        runs.append(row)
    return runs


@app.get("/api/agent-runs")
def all_agent_runs(limit: int = 200) -> dict[str, Any]:
    """Every node run, newest last, with the campaign each belongs to.

    Observability is its own surface, not a tab hanging off one thread: the
    question "which node failed" is usually asked ACROSS runs, and tying the
    view to a thread means you cannot see a run whose thread you have not
    opened."""
    if not _debug_enabled():
        raise HTTPException(404, "observability is disabled on this deployment")

    series_names = {row["id"]: row["name"] for row in store.list_series()}
    names = {
        t["id"]: series_names.get(t["series_id"], "")
        for t in store.list_threads()
    }

    runs = _read_runs()
    for row in runs:
        tid = row.get("thread_id")
        row["campaign_name"] = names.get(tid or "", None)
    return {"runs": runs[-limit:]}
