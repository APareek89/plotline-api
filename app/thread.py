"""Addendum-01 §01: the conversational driver for chat-first Content Studio.

The agent drives the sequence — inspiration → format options (PASS 0.5) →
concepts in batches of 3-4 → plan — inside a thread. All approvals happen
in-thread via UserEvents; buttons and typed commands are the same signal.

Every agent turn is an AgentMessage envelope persisted to thread_messages;
the UI renders artifacts as cards and polls for new messages. Labeled
working-steps (never a bare spinner) come from the in-memory `working` map.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Optional

from app import ccs as ccs_mod
from app import store
from app.agents.mock import _niche_of
from app.agents.runner import AgentHardFail
from app.commands import parse_command
from app.orchestrator import _persist, regenerate_concept, run_formats, run_pipeline
from app.rag_client import RagUnavailable, rag
from app.schemas import (
    AgentMessage,
    ArtifactEnvelope,
    CreatorContext,
    Escalation,
    UserEvent,
    objective_family,
)
from app.validators import (
    NICHE_MIN_ASSETS,
    NICHE_MIN_CHUNKS,
    THIN_PLAN_RATIO,
)

BATCH_SIZE = 3  # concepts per message: 3-4, never all at once, never one-by-one

# thread_id → current labeled step (in-memory; single-process dev server)
_working: dict[str, str] = {}
_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def working_step(thread_id: str) -> Optional[str]:
    return _working.get(thread_id)


def _thread_lock(thread_id: str) -> threading.Lock:
    with _lock_guard:
        return _locks.setdefault(thread_id, threading.Lock())


def _say(
    thread_id: str,
    text: str,
    artifacts: Optional[list[ArtifactEnvelope]] = None,
    question: Optional[str] = None,
) -> None:
    msg = AgentMessage(thread_id=thread_id, text=text, artifacts=artifacts or [], question=question)
    store.append_message(thread_id, "agent", msg.model_dump(mode="json"))


def _actions(*pairs: tuple[str, str, str]) -> list[dict[str, Any]]:
    return [{"id": e, "label": label, "style": style, "event": e} for e, label, style in pairs]


CONCEPT_ACTIONS = _actions(
    ("approve", "Approve", "primary"),
    ("feedback", "Feedback", "secondary"),
    ("regenerate", "Regenerate", "danger"),
)


# ------------------------------------------------------------ thread start --


def start_planning_thread(series_id: str) -> dict[str, Any]:
    """Create thread — NN, pin the context summary, kick off the agent."""
    series = store.get_series(series_id)
    if not series:
        raise ValueError("series not found")
    thread = store.create_thread(series_id, kind="planning")
    t = threading.Thread(target=_opening_turn, args=(series_id, thread["id"]), daemon=True)
    t.start()
    return thread


def _niche_counts(context: CreatorContext) -> tuple[int, int]:
    """Niche coverage gate inputs (§7.3): inspiration assets + platform-matched
    KB chunks for this niche. Honest counts from live retrieval."""
    niche = _niche_of(context.content_area)
    try:
        assets = rag.search_corpus("", k=50, filters={"kind": "asset", "niche": niche})
        chunks = rag.search_corpus(context.content_area, k=50, filters={"kind": "chunk"})
        return len(assets), len(chunks)
    except RagUnavailable:
        return 0, 0


def _opening_turn(series_id: str, thread_id: str) -> None:
    try:
        series = store.get_series(series_id)
        context = CreatorContext.model_validate(series["context"])
        _working[thread_id] = "reading your context"

        # 1. pinned, editable context summary (the form, collapsed)
        asset_count, chunk_count = _niche_counts(context)
        provisional_niche = asset_count < NICHE_MIN_ASSETS or chunk_count < NICHE_MIN_CHUNKS
        ctx_payload = {
            "context": context.model_dump(mode="json"),
            "pinned": True,
            "niche_coverage": {
                "assets": asset_count, "chunks": chunk_count,
                "provisional": provisional_niche,
                "thresholds": {"assets": NICHE_MIN_ASSETS, "chunks": NICHE_MIN_CHUNKS},
            },
        }
        note = (
            " Heads up: this niche is below coverage thresholds, so plans run provisional until the corpus grows."
            if provisional_niche else ""
        )
        _say(
            thread_id,
            f"Context locked in.{note}",
            [ArtifactEnvelope(type="context_summary", id="ctx", title=context.name,
                              payload=ctx_payload,
                              actions=_actions(("edit_context", "Edit", "secondary")))],
        )

        # 2. inspiration set (v1 launch state: wired retrieval, sample corpus,
        #    selection disabled, honest label)
        _working[thread_id] = "retrieving evidence"
        niche = _niche_of(context.content_area)
        cards = rag.search_corpus(context.description or context.content_area, k=8,
                                  filters={"kind": "asset", "niche": niche})
        _say(
            thread_id,
            "Here's what's working in your niche right now.",
            [ArtifactEnvelope(type="inspiration_set", id="insp", title="Reference set",
                              payload={"sample_data": True, "selection_enabled": False, "cards": cards})],
        )

        # 3. PASS 0.5 — format options, then wait for the pick
        _working[thread_id] = "drafting format options"
        run_retrieved: set[str] = set()
        formats = run_formats(context, run_retrieved)
        _say(
            thread_id,
            "Before any concepts: the series needs a repeatable format.",
            [ArtifactEnvelope(
                type="format_options", id="formats", title="Format options",
                payload=formats.model_dump(mode="json"),
                actions=[{"id": f"pick_{o.format_id}", "label": f"Use {o.name}",
                          "style": "primary", "event": f"pick_{o.format_id}"}
                         for o in formats.options],
            )],
            question="Which format should the series run on? Pick one or more, or tell me what to change.",
        )
        store.set_thread_stage(thread_id, "formats")
        for artifact_id in ("ctx", "insp", "formats"):
            store.log_artifact_activity(thread_id, artifact_id, "proposed")
    except Exception as exc:
        _error_card(thread_id, f"Couldn't open the thread: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


# ------------------------------------------------------------- user events --


def handle_event(event: UserEvent) -> None:
    """Both input paths land here. Runs synchronously for acks; long work
    (planning, regeneration) continues on a background thread."""
    thread = store.get_thread(event.thread_id)
    if not thread:
        raise ValueError("thread not found")
    store.append_message(event.thread_id, "user", event.model_dump(mode="json"))

    if event.type == "action":
        _dispatch(thread, event.action.artifact_id, event.action.event, None)
        return

    # typed path → parse to the same events (§05: neither path is second-class)
    batch = _last_batch_order(event.thread_id)
    parsed = parse_command(event.text or "", panel_focus=event.panel_focus, batch_order=batch)
    if parsed is None:
        _say(
            event.thread_id,
            "I handle approvals, regenerations and format picks right now.",
            question='Try "approve c2", "regenerate c3 — punchier hook", or "pick f1".',
        )
        return
    if parsed["event"] == "approve_plan":
        _dispatch(thread, "plan", "approve", None)
    elif parsed["event"] == "pick_format":
        _dispatch(thread, "formats", "pick", parsed["targets"])
    else:
        for cid in parsed["targets"]:
            _dispatch(thread, cid, "approve" if parsed["event"] == "approve_concept" else "regenerate",
                      parsed["note"])


def _dispatch(thread: dict[str, Any], artifact_id: str, event: str, extra: Any) -> None:
    thread_id = thread["id"]
    series_id = thread["series_id"]

    if artifact_id == "formats" or event.startswith("pick"):
        picks = extra if isinstance(extra, list) else [event.removeprefix("pick_")]
        picks = [p for p in picks if p and p != "pick"]
        store.set_thread_formats(thread_id, picks)
        store.log_artifact_activity(thread_id, "formats", "approved", f"chose {', '.join(picks)}")
        t = threading.Thread(target=_plan_turn, args=(series_id, thread_id, picks), daemon=True)
        t.start()
        return

    if artifact_id == "plan" and event == "approve":
        store.set_series_status(series_id, "approved")
        store.log_artifact_activity(thread_id, "plan", "approved")
        store.set_thread_stage(thread_id, "done")
        _say(thread_id, "Plan approved — it's live in Plans. This thread stays as the series' planning record.")
        return

    if event == "approve":
        store.set_concept_approved(series_id, artifact_id, True)
        store.log_artifact_activity(thread_id, artifact_id, "approved")
        _say(thread_id, f"{artifact_id} approved.")
        return

    if event in ("regenerate", "feedback"):
        note = extra or ""
        store.log_artifact_activity(thread_id, artifact_id, "refine_requested", note or None)
        t = threading.Thread(target=_regen_turn, args=(series_id, thread_id, artifact_id, note), daemon=True)
        t.start()
        return

    _say(thread_id, f"Unknown action {event!r} on {artifact_id} — nothing changed.")


# ---------------------------------------------------------- planning turns --


def _concept_artifact(concept: dict, verdict: Optional[dict], state: dict, options: Optional[dict]) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        type="concept", id=concept["id"], title=concept["title"],
        payload={
            "concept": concept,
            "verdict": verdict,
            "options": options,
            "ccs": state.get("ccs"),
            "status": state.get("status"),
            "coverage": state.get("coverage"),
            "provisional": state.get("provisional", False),
        },
        actions=CONCEPT_ACTIONS,
    )


def _plan_turn(series_id: str, thread_id: str, picks: list[str]) -> None:
    try:
        series = store.get_series(series_id)
        context = CreatorContext.model_validate(series["context"])
        store.set_series_status(series_id, "planning")
        store.set_thread_stage(thread_id, "planning")
        asset_count, _chunks = _niche_counts(context)

        def emit(stage: str, message: str) -> None:
            _working[thread_id] = message

        plan, feedback, options, statuses = run_pipeline(
            context, emit, chosen_formats=picks, niche_asset_count=asset_count,
        )
        _persist(series_id, plan, feedback, options, statuses)

        verdicts = {v.concept_id: v.model_dump(mode="json") for v in feedback.concept_verdicts}
        opts = {o.concept_id: o.model_dump(mode="json") for o in options.concept_options}
        concepts = [c.model_dump(mode="json") for c in plan.concepts]

        # concepts in batches of 3-4, each batch framed in one line (§01)
        total = len(concepts)
        for start in range(0, total, BATCH_SIZE):
            batch = concepts[start : start + BATCH_SIZE]
            artifacts = [
                _concept_artifact(c, verdicts.get(c["id"]), statuses.get(c["id"], {}), opts.get(c["id"]))
                for c in batch
            ]
            frame = f"Concepts {start + 1}–{start + len(batch)} of {total} — judged, scored, options attached."
            _say(thread_id, frame, artifacts)
            for c in batch:
                store.log_artifact_activity(thread_id, c["id"], "proposed", f"CCS {statuses.get(c['id'], {}).get('ccs')}")
                v = verdicts.get(c["id"])
                if v:
                    downs = [ev for ev in v["element_verdicts"] if ev["verdict"] == "downgrade"]
                    for d in downs:
                        store.log_artifact_activity(thread_id, c["id"], "downgraded",
                                                    f"{d['element']}: {d.get('reason')}")
        if plan.changes:
            for c in plan.concepts:
                relevant = [ch for ch in plan.changes if ch.startswith(c.id)]
                for ch in relevant:
                    store.log_artifact_activity(thread_id, c.id, "refined", ch)

        # thin-plan escalation (§7.3): >40% below qualify after the one refine
        below = sum(1 for s in statuses.values() if (s.get("ccs") or 0) < ccs_mod.QUALIFY_THRESHOLD)
        artifacts: list[ArtifactEnvelope] = []
        if total and below / total > THIN_PLAN_RATIO:
            esc = Escalation(
                reason=f"{below} of {total} concepts scored below {ccs_mod.QUALIFY_THRESHOLD} after the single refine pass",
                below_threshold_count=below, total_concepts=total,
                choices=["seed_inspiration", "broaden_niche", "accept_provisional"],
            )
            artifacts.append(ArtifactEnvelope(
                type="escalation", id="esc", title="This plan is thin",
                payload=esc.model_dump(mode="json"),
                actions=_actions(("accept_provisional", "Accept provisional", "secondary"),
                                 ("broaden_niche", "Broaden niche", "secondary")),
            ))

        cov_vals = [s.get("coverage") for s in statuses.values() if s.get("coverage") is not None]
        plan_cov = round(sum(cov_vals) / len(cov_vals)) if cov_vals else 0
        artifacts.append(ArtifactEnvelope(
            type="plan", id="plan", title=f"{context.name} — plan",
            payload={
                "plan": plan.model_dump(mode="json"),
                "statuses": statuses,
                "coverage": plan_cov,
                "provisional": ccs_mod.is_provisional(plan_cov),
            },
            actions=_actions(("approve", "Approve plan", "primary")),
        ))
        _say(thread_id, "The full timeline, with every score recomputed server-side.",
             artifacts, question="Approve the plan, or work the concepts first?")
        store.log_artifact_activity(thread_id, "plan", "proposed", f"coverage {plan_cov}%")
        store.set_series_status(series_id, "awaiting_review")
        store.set_thread_stage(thread_id, "review")
    except AgentHardFail as exc:
        store.set_series_status(series_id, "context_ready")
        _error_card(thread_id, "Validation kept failing after retries — nothing was silently accepted.", str(exc))
    except Exception as exc:
        store.set_series_status(series_id, "context_ready")
        _error_card(thread_id, f"Pipeline error: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _regen_turn(series_id: str, thread_id: str, concept_id: str, note: str) -> None:
    try:
        _working[thread_id] = f"regenerating {concept_id}"
        run_id = store.create_run(series_id, "regenerate_concept")
        regenerate_concept(series_id, concept_id, note, run_id)  # reuses engine + validators

        stored = store.get_plan(series_id)
        states = {s["concept_id"]: s for s in store.get_concept_states(series_id)}
        concept = next((c for c in stored["plan"]["concepts"] if c["id"] == concept_id), None)
        verdict = next((v for v in (stored["feedback"] or {}).get("concept_verdicts", [])
                        if v["concept_id"] == concept_id), None)
        opts = next((o for o in (stored["options"] or {}).get("concept_options", [])
                     if o["concept_id"] == concept_id), None)
        state = dict(states.get(concept_id, {}))
        # recompute coverage for the refreshed card
        context = CreatorContext.model_validate(store.get_series(series_id)["context"])
        from app.schemas import Concept
        if concept:
            cov = ccs_mod.evidence_coverage(objective_family(context.objective), Concept.model_validate(concept))
            state["coverage"] = cov
            state["provisional"] = ccs_mod.is_provisional(cov)
            store.log_artifact_activity(thread_id, concept_id, "refined", note or "regenerated")
            _say(thread_id, f"{concept_id} regenerated and re-judged.",
                 [_concept_artifact(concept, verdict, state, opts)])
        else:
            _error_card(thread_id, f"{concept_id} not found after regeneration", "")
    except Exception as exc:
        _error_card(thread_id, f"Regeneration failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


# ---------------------------------------------------------------- helpers --


def _last_batch_order(thread_id: str) -> list[str]:
    """Concept ids in the most recent concept batch — lets 'the second one'
    resolve without ids."""
    for msg in reversed(store.get_messages(thread_id)):
        if msg["role"] != "agent":
            continue
        ids = [a["id"] for a in msg["envelope"].get("artifacts", []) if a.get("type") == "concept"]
        if ids:
            return ids
    return []


def _error_card(thread_id: str, summary: str, detail: str) -> None:
    """§04: what failed + one Retry action, never a stack trace in the card."""
    _say(
        thread_id,
        "Something broke — honestly.",
        [ArtifactEnvelope(type="escalation", id="error", title=summary,
                          payload={"reason": summary, "detail_logged": bool(detail),
                                   "below_threshold_count": 0, "total_concepts": 1,
                                   "choices": ["accept_provisional"]},
                          actions=_actions(("retry", "Retry", "primary")))],
    )
