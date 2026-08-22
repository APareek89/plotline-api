"""plotline-api — orchestrator + agents (§7 build handoff: Claude Code lane).

Consumes the plotline-rag HTTP contract; does not embed, index, or query
vectors itself. Run: uvicorn app.main:app --port 8600
"""
from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import config, orchestrator, store
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3100", "http://127.0.0.1:3100"],
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
    return store.list_series()


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
    try:
        thread_driver.handle_event(body)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.get("/api/threads/{thread_id}/artifacts/{artifact_id}/activity")
def artifact_activity(thread_id: str, artifact_id: str) -> list[dict[str, Any]]:
    return store.get_artifact_activity(thread_id, artifact_id)


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
