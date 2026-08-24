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
from pydantic import BaseModel, Field

from app import brand_extract, campaign, config, creative, orchestrator, store
from app import thread as thread_driver
from app.agents.runner import AgentHardFail
from app.orchestrator import run_events
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


@app.post("/api/series")
def create_series(body: SeriesCreateBody) -> dict[str, Any]:
    if not (body.form.get("name") or "").strip():
        raise HTTPException(422, "Naming is required: every series/post needs a name")
    try:
        context = orchestrator.run_intake(body.form, body.upload_ids)
    except AgentHardFail as exc:
        raise HTTPException(422, f"Context incomplete: {exc.errors}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Intake failed: {exc}") from exc
    series_id = store.create_series(context.model_dump(mode="json"))
    # Addendum-01 §01: the form morphs into a thread — planning is chat-first.
    thread = thread_driver.start_planning_thread(series_id)
    return {"id": series_id, "context": context.model_dump(mode="json"), "thread": thread}


@app.get("/api/series")
def list_series() -> list[dict[str, Any]]:
    """Content series only. A campaign is a series row too, but it carries a
    CampaignContext the content surfaces can't render — GET /api/campaigns is
    the authoritative list for those."""
    campaign_ids = {thread["series_id"] for thread in store.list_threads(kind="campaign")}
    return [series for series in store.list_series() if series["id"] not in campaign_ids]


@app.get("/api/series/{series_id}")
def get_series(series_id: str) -> dict[str, Any]:
    series = store.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    plan = store.get_plan(series_id)
    states = store.get_concept_states(series_id)
    return {**series, "plan_bundle": plan, "concept_states": states}


class ContextUpdateBody(BaseModel):
    form: dict[str, Any]
    upload_ids: list[str] = Field(default_factory=list)


@app.put("/api/series/{series_id}/context")
def update_context(series_id: str, body: ContextUpdateBody) -> dict[str, Any]:
    series = store.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    context = orchestrator.run_intake(body.form, body.upload_ids)
    store.update_series_context(series_id, context.model_dump(mode="json"))
    return {"id": series_id, "context": context.model_dump(mode="json")}


# -------------------------------------------------------------- inspiration --


@app.get("/api/series/{series_id}/inspiration")
def suggested_inspiration(series_id: str) -> dict[str, Any]:
    """Suggested reference cards. Launch state (§3.2): wired retrieval call,
    sample corpus, selection disabled in the UI with an honest label."""
    series = store.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    context = CreatorContext.model_validate(series["context"])
    from app.agents.mock import _niche_of  # niche heuristic shared with mock agents

    niche = _niche_of(context.content_area)
    try:
        results = rag.search_corpus(
            context.description or context.content_area,
            k=12,
            filters={"kind": "asset", "niche": niche},
        )
        if len(results) < 6:
            more = rag.search_corpus(context.content_area, k=12 - len(results), filters={"kind": "asset"})
            seen = {r["source_id"] for r in results}
            results += [r for r in more if r["source_id"] not in seen]
    except RagUnavailable as exc:
        raise HTTPException(503, f"RAG service unavailable: {exc}") from exc
    return {"sample_data": True, "selection_enabled": False, "cards": results}


# -------------------------------------------- Addendum-01 §01/§02: threads --


@app.get("/api/series/{series_id}/threads")
def series_threads(series_id: str) -> list[dict[str, Any]]:
    if not store.get_series(series_id):
        raise HTTPException(404, "series not found")
    return store.get_series_threads(series_id)


@app.get("/api/threads/{thread_id}")
def get_thread(thread_id: str, after_seq: int = 0) -> dict[str, Any]:
    thread = store.get_thread(thread_id)
    if not thread:
        raise HTTPException(404, "thread not found")
    series = store.get_series(thread["series_id"])
    return {
        **thread,
        "series_name": series["name"] if series else "",
        "working": thread_driver.working_step(thread_id),  # labeled step, never a bare spinner
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
    try:
        if thread["kind"] == "creative":
            creative.handle_event(body)
        elif thread["kind"] == "campaign":
            campaign.handle_event(body)
        else:
            thread_driver.handle_event(body)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.get("/api/threads/{thread_id}/artifacts/{artifact_id}/activity")
def artifact_activity(thread_id: str, artifact_id: str) -> list[dict[str, Any]]:
    return store.get_artifact_activity(thread_id, artifact_id)


# ----------------------------- Addendum-02: Creative Studio + Post Cards ----


class ProduceBody(BaseModel):
    option: str = "A"


@app.post("/api/series/{series_id}/concepts/{concept_id}/produce")
def produce(series_id: str, concept_id: str, body: ProduceBody) -> dict[str, Any]:
    """Plans 'Generate next post' + concept-card '→ Creative Studio' land here."""
    try:
        return creative.start_creative_thread(series_id, concept_id, body.option)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


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


@app.get("/api/post-cards")
def post_cards(series_id: Optional[str] = None) -> list[dict[str, Any]]:
    return store.list_post_cards(series_id)


@app.get("/api/post-cards/{card_id}")
def post_card(card_id: str) -> dict[str, Any]:
    card = store.get_post_card(card_id)
    if not card:
        raise HTTPException(404, "post card not found")
    return card


@app.get("/api/post-cards/{card_id}/bundle")
def post_card_bundle(card_id: str):
    """§04: Download bundle — zip of media + caption_<platform>.txt + meta.json."""
    import io
    import zipfile

    from fastapi.responses import StreamingResponse

    card = store.get_post_card(card_id)
    if not card:
        raise HTTPException(404, "post card not found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for platform, text in card["post_content"]["caption_variants"].items():
            hashtags = " ".join(card["post_content"]["hashtags"])
            zf.writestr(f"caption_{platform}.txt", f"{text}\n\n{hashtags}")
        zf.writestr("meta.json", json.dumps(card, indent=2, default=str))
        for m in card["media"]:
            asset_id = m["url"].rstrip("/").split("/")[-2] if m["url"].endswith("/file") else None
            asset = store.get_asset(asset_id) if asset_id else None
            if asset:
                from pathlib import Path as _P

                p = _P(asset["path"])
                if p.exists():
                    zf.writestr(f"media/{m['params'].get('prompt_id', p.stem)}{p.suffix}", p.read_bytes())
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{card_id}.zip"'})


@app.get("/api/threads/{thread_id}/generation-log")
def generation_log(thread_id: str) -> list[dict[str, Any]]:
    return store.get_generation_log(thread_id)


@app.post("/api/post-cards/{card_id}/mark-posted")
def mark_posted(card_id: str) -> dict[str, Any]:
    """One object, three surfaces: posting from Plans/My Space is the same
    status change the thread action makes."""
    import time as _time

    card = store.get_post_card(card_id)
    if not card:
        raise HTTPException(404, "post card not found")
    card["status"] = "posted"
    card["posted_at"] = _time.time()
    store.save_post_card(card)
    store.set_production_status(card["series_id"], card["concept_id"], "posted")
    return card


class PromptEditBody(BaseModel):
    prompt_text: str


@app.post("/api/threads/{thread_id}/prompts/{slot}")
def edit_prompt(thread_id: str, slot: str, body: PromptEditBody) -> dict[str, Any]:
    """§08 rule 7: the user's edited prompt is used VERBATIM downstream."""
    ws = creative._pending(thread_id)
    if slot not in ws.get("prompts", {}):
        raise HTTPException(404, f"no pending prompt for slot {slot}")
    ws["prompts"][slot] = body.prompt_text
    store.log_generation(thread_id, None, "edit_prompt", prompt=body.prompt_text)
    store.log_artifact_activity(thread_id, f"prompt_{slot}", "refined", "user edited prompt")
    return {"slot": slot, "prompt_text": body.prompt_text}


class DiyBody(BaseModel):
    kind: str  # image | video | audio
    prompt: str
    model_tier: Literal["draft", "final", "pro"] = "final"  # images; the fixed stack, not a model id
    ratio: str = "9:16"
    duration_s: float = 4.0


@app.post("/api/diy/generate")
def diy_generate(body: DiyBody) -> dict[str, Any]:
    """§06 DIY: same prompt-artifact → asset pair, cost shown, no agent.
    model_tier picks among the FIXED stack (config.MEDIA_MODELS image_draft/
    final/pro) — it is not model selection, which stays unavailable behind the
    disabled Settings gear."""
    from app.fal_client import MediaError, estimate_cost, generate

    cost = estimate_cost(body.kind, duration_s=body.duration_s,
                         chars=len(body.prompt), tier=body.model_tier)
    try:
        out = generate(body.kind, body.prompt, ratio=body.ratio,
                       duration_s=body.duration_s, tier=body.model_tier)
    except MediaError as exc:
        raise HTTPException(502 if not exc.policy else 422, str(exc)) from exc
    asset_id = store.add_asset(None, "diy", body.kind, out["path"],
                               {"model": out["model"], "prompt": body.prompt, "diy": True},
                               out["cost"])
    store.log_generation(None, asset_id, "generate", prompt=body.prompt,
                         model=out["model"], cost=out["cost"])
    return {"asset_id": asset_id, "url": f"/api/assets/{asset_id}/file",
            "model": out["model"], "cost": out["cost"], "estimated": cost, "mock": out["mock"]}


@app.get("/api/diy/assets")
def diy_assets() -> list[dict[str, Any]]:
    return [a for a in store.list_assets() if a["params"].get("diy")]


# --------------------------- Addendum-03: Marketing Studio — campaigns ------

CAMPAIGN_BLOCKS = ("product", "campaign", "brand")


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
            "spend_credits": round(store.campaign_spend(series_id) / creative.CREDIT_USD, 1),
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
        "cards_done": campaign.cards_done(series["context"]),
        "threads": store.get_series_threads(campaign_id),
        "ad_cards": store.list_ad_cards(campaign_id),
    }


@app.put("/api/campaigns/{campaign_id}/blocks/{block}")
def put_campaign_block(campaign_id: str, block: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Step 2 cards / path-b elicitation — both write the identical schema."""
    _campaign_or_404(campaign_id)
    if block not in CAMPAIGN_BLOCKS:
        raise HTTPException(422, f"unknown block '{block}' — expected one of {list(CAMPAIGN_BLOCKS)}")
    try:
        context = campaign.save_block(campaign_id, block, body)
    except ValueError as exc:  # pydantic ValidationError included
        raise HTTPException(422, f"{block} details invalid: {exc}") from exc
    return {"context": context, "cards_done": campaign.cards_done(context)}


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
    missing = campaign.missing_blocks(series["context"])
    if missing:
        raise HTTPException(422, f"Campaign context incomplete — still needed: {', '.join(missing)}")
    try:
        campaign.begin_rumination(campaign_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


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


@app.post("/api/series/{series_id}/generate")
def generate(series_id: str) -> dict[str, Any]:
    series = store.get_series(series_id)
    if not series:
        raise HTTPException(404, "series not found")
    run_id = orchestrator.start_plan_run(series_id)
    return {"run_id": run_id}


class RegenBody(BaseModel):
    feedback: str = ""


@app.post("/api/series/{series_id}/concepts/{concept_id}/regenerate")
def regenerate(series_id: str, concept_id: str, body: RegenBody) -> dict[str, Any]:
    if not store.get_series(series_id):
        raise HTTPException(404, "series not found")
    run_id = orchestrator.start_regen_run(series_id, concept_id, body.feedback)
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/events")
async def run_event_stream(run_id: str) -> StreamingResponse:
    async def stream():
        cursor = 0
        idle = 0.0
        while True:
            events, cursor = run_events.since(run_id, cursor)
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("terminal"):
                    return
            if events:
                idle = 0.0
            else:
                idle += 0.3
                if idle > 300:  # orphaned stream safety valve
                    yield f"data: {json.dumps({'stage': 'error', 'message': 'stream timeout', 'terminal': True})}\n\n"
                    return
            await asyncio.sleep(0.3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------ concept-level moves --


@app.post("/api/series/{series_id}/concepts/{concept_id}/approve")
def approve(series_id: str, concept_id: str) -> dict[str, Any]:
    store.set_concept_approved(series_id, concept_id, True)
    states = store.get_concept_states(series_id)
    if states and all(s["approved"] or s["status"] == "rework" for s in states):
        store.set_series_status(series_id, "locked")
    return {"ok": True}


@app.post("/api/series/{series_id}/concepts/{concept_id}/unapprove")
def unapprove(series_id: str, concept_id: str) -> dict[str, Any]:
    store.set_concept_approved(series_id, concept_id, False)
    plan = store.get_plan(series_id)
    if plan:
        store.set_series_status(series_id, "awaiting_review")
    return {"ok": True}


class ReorderBody(BaseModel):
    ordered_ids: list[str]


@app.post("/api/series/{series_id}/reorder")
def reorder(series_id: str, body: ReorderBody) -> dict[str, Any]:
    store.reorder_concepts(series_id, body.ordered_ids)
    return {"ok": True}


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
