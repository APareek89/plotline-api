"""Thin custom orchestrator (§6): explicit state machine driving Anthropic
tool-use calls — context → retrieve → plan → critique → refine → await-approval.
Hard cap: one refine loop (cost + latency control).
"""
from __future__ import annotations

import base64
import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Optional

from app import ccs as ccs_mod
from app import config, store
from app.agents import mock as mock_agents
from app.agents.runner import AgentHardFail, run_agent
from app.rag_client import rag
from app.schemas import (
    CreatorContext,
    Feedback,
    ObjectiveFamily,
    OptionsOutput,
    Plan,
    objective_family,
)
from app.tools import ToolDispatcher
from app.validators import (
    validate_feedback,
    validate_intake,
    validate_options,
    validate_plan,
)

# ------------------------------------------------------------- SSE event bus


class RunEvents:
    """In-memory per-run progress feed consumed by the SSE endpoint."""

    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def emit(self, run_id: str, stage: str, message: str, **extra: Any) -> None:
        event = {"ts": time.time(), "stage": stage, "message": message, **extra}
        with self._lock:
            self._events.setdefault(run_id, []).append(event)

    def since(self, run_id: str, cursor: int) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            events = self._events.get(run_id, [])
            return events[cursor:], len(events)


run_events = RunEvents()


def _dispatcher(
    surfaced_ids: Optional[set[str]] = None,
    context: Optional[CreatorContext] = None,
) -> ToolDispatcher:
    # Addendum-01 §7.3: retrieval hard-filters platform to the context's set.
    platform_filter = list(context.platforms) if context and context.platforms else None
    return ToolDispatcher(
        rag, store.get_profile, surfaced_ids=surfaced_ids, platform_filter=platform_filter
    )


# ------------------------------------------------------------------- intake


def run_intake(form: dict[str, Any], upload_ids: list[str]) -> CreatorContext:
    uploads = store.get_uploads(upload_ids)
    upload_meta = [{"filename": u["filename"], "kind": u["kind"]} for u in uploads]
    has_files = bool(uploads)

    extra_blocks: list[dict[str, Any]] = []
    if has_files and not config.MOCK_LLM:
        extra_blocks = _vision_blocks(uploads)

    model = config.INTAKE_VISION_MODEL if has_files else config.INTAKE_MODEL
    context, _log = run_agent(
        agent="intake",
        prompt_name="intake",
        model=model,
        user_payload={"form": form, "uploads": upload_meta},
        schema=CreatorContext,
        dispatcher=None,
        validate=validate_intake,
        mock_fn=mock_agents.mock_intake,
        use_tools=False,
        extra_content_blocks=extra_blocks or None,
    )
    return context


def _vision_blocks(uploads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude's native vision handles reference uploads — no separate vision
    model (§3.1). Images and PDFs only; other files are described by metadata."""
    blocks: list[dict[str, Any]] = []
    for upload in uploads[:6]:
        path = Path(upload["path"])
        if not path.exists():
            continue
        content_type = upload.get("content_type") or ""
        data = base64.standard_b64encode(path.read_bytes()).decode()
        if content_type.startswith("image/"):
            blocks.append(
                {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": data}}
            )
        elif content_type == "application/pdf":
            blocks.append(
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
            )
    return blocks


# ----------------------------------------------------------- plan pipeline


def _persona_for(context: CreatorContext) -> str:
    audience = context.target_audience or "the stated target audience"
    platforms = ", ".join(context.platforms) or "short-form video platforms"
    return (
        f"a skeptical, scroll-happy member of {audience} on {platforms}, fluent in "
        f"{context.content_area} content norms, who has seen every recycled format twice"
    )


def run_formats(context: CreatorContext, run_retrieved: set[str]) -> "FormatOptions":
    """Addendum-01 PASS 0.5: 2-3 repeatable formats before any concepts."""
    from app.schemas import FormatOptions
    from app.validators import validate_formats

    formats, _ = run_agent(
        agent="planner.formats",
        prompt_name="planner",
        model=config.PLANNER_MODEL,
        user_payload={"context": context.model_dump(mode="json"), "pass": "formats"},
        schema=FormatOptions,
        dispatcher=_dispatcher(run_retrieved, context=context),
        validate=lambda f: validate_formats(f, rag, retrieved_ids=run_retrieved),
        mock_fn=mock_agents.mock_planner_formats,
    )
    return formats


def run_pipeline(
    context: CreatorContext,
    emit,
    *,
    chosen_formats: Optional[list[str]] = None,
    niche_asset_count: Optional[int] = None,
    run_retrieved: Optional[set[str]] = None,
) -> tuple[Plan, Feedback, OptionsOutput, dict[str, dict[str, Any]]]:
    """The §3.4 pipeline core, shared by the legacy stage flow and the
    Addendum-01 thread driver. `emit(stage, message)` reports labeled steps."""
    family = objective_family(context.objective)
    emit("retrieve", "Pulling inspiration references + benchmarks…")
    rag.ensure_ready()  # dependency-unavailable surfaces here, before any agent call
    payload = {"context": context.model_dump(mode="json")}
    if chosen_formats:
        payload["chosen_formats"] = chosen_formats
    if niche_asset_count is not None:
        payload["niche_asset_count"] = niche_asset_count
    # One citation allowlist for the whole pipeline run: agents may cite
    # only source_ids their retrieval tools surfaced within THIS run.
    if run_retrieved is None:
        run_retrieved = set()

    emit("plan", "Planner drafting concepts with evidence…")
    dispatcher = _dispatcher(run_retrieved, context=context)
    plan, _ = run_agent(
        agent="planner.concepts",
        prompt_name="planner",
        model=config.PLANNER_MODEL,
        user_payload={**payload, "pass": "concepts"},
        schema=Plan,
        dispatcher=dispatcher,
        validate=lambda p: validate_plan(p, context, rag, retrieved_ids=run_retrieved),
        mock_fn=mock_agents.mock_planner_concepts,
    )

    emit("critique", "Red-team feedback agent judging every element…")
    feedback, _ = run_agent(
        agent="feedback.judge",
        prompt_name="feedback",
        model=config.FEEDBACK_MODEL,
        user_payload={**payload, "plan": plan.model_dump(mode="json")},
        schema=Feedback,
        dispatcher=_dispatcher(run_retrieved, context=context),
        validate=lambda f: validate_feedback(
            f, plan, context, rag, retrieved_ids=run_retrieved,
            niche_asset_count=niche_asset_count,
        ),
        prompt_replacements={"dynamic_persona": _persona_for(context)},
        mock_fn=mock_agents.mock_feedback,
    )

    # ---- refine (hard cap: 1 loop; only flagged concepts may change)
    flagged = _flagged_ids(family, feedback)
    if flagged:
        emit("refine", f"Refining {len(flagged)} flagged concept(s) — one pass, diff-checked…")
        refined_plan, _ = run_agent(
            agent="planner.refine",
            prompt_name="planner",
            model=config.PLANNER_MODEL,
            user_payload={
                **payload,
                "pass": "refine",
                "plan": plan.model_dump(mode="json"),
                "feedback": feedback.model_dump(mode="json"),
                "flagged_concept_ids": sorted(flagged),
            },
            schema=Plan,
            dispatcher=_dispatcher(run_retrieved, context=context),
            validate=lambda p: validate_plan(
                p, context, rag, previous_plan=plan, flagged_concept_ids=flagged,
                retrieved_ids=run_retrieved,
            ),
            mock_fn=mock_agents.mock_planner_refine,
        )
        plan = refined_plan

        emit("critique", "Feedback agent re-judging refined concepts…")
        refined_feedback, _ = run_agent(
            agent="feedback.rejudge",
            prompt_name="feedback",
            model=config.FEEDBACK_MODEL,
            user_payload={
                **payload,
                "plan": plan.model_dump(mode="json"),
                "only_concept_ids": sorted(flagged),
                "refined": True,
            },
            schema=Feedback,
            dispatcher=_dispatcher(run_retrieved, context=context),
            validate=lambda f: validate_feedback(
                f, _subset_plan(plan, flagged), context, rag, retrieved_ids=run_retrieved,
                niche_asset_count=niche_asset_count,
            ),
            prompt_replacements={"dynamic_persona": _persona_for(context)},
            mock_fn=mock_agents.mock_feedback,
        )
        feedback = _merge_feedback(feedback, refined_feedback)

    # ---- statuses + options
    statuses = _statuses(family, feedback)
    for concept in plan.concepts:  # Addendum-01 §7.3: coverage % beside CCS everywhere
        if concept.id in statuses:
            cov = ccs_mod.evidence_coverage(family, concept)
            statuses[concept.id]["coverage"] = cov
            statuses[concept.id]["provisional"] = ccs_mod.is_provisional(cov)
    qualified = {cid for cid, s in statuses.items() if s["status"] in ("qualified", "strong")}

    options = OptionsOutput(concept_options=[])
    if qualified:
        emit("options", f"Generating 3 creative options for {len(qualified)} qualified concept(s)…")
        qualified_concepts = [
            {**c.model_dump(mode="json"), "ccs_final": statuses[c.id]["ccs"]}
            for c in plan.concepts
            if c.id in qualified
        ]
        options, _ = run_agent(
            agent="planner.options",
            prompt_name="planner",
            model=config.PLANNER_MODEL,
            user_payload={**payload, "pass": "options", "concepts": qualified_concepts},
            schema=OptionsOutput,
            dispatcher=_dispatcher(run_retrieved, context=context),
            validate=lambda o: validate_options(o, qualified),
            mock_fn=mock_agents.mock_planner_options,
        )

    return plan, feedback, options, statuses


def generate_plan(series_id: str, run_id: str) -> None:
    """Legacy stage flow (pre-addendum UI + evals): full pipeline → persist."""
    try:
        series = store.get_series(series_id)
        if not series:
            raise RuntimeError(f"series {series_id} not found")
        context = CreatorContext.model_validate(series["context"])
        store.set_series_status(series_id, "planning")

        plan, feedback, options, statuses = run_pipeline(
            context, lambda stage, msg: run_events.emit(run_id, stage, msg)
        )

        _persist(series_id, plan, feedback, options, statuses)
        store.set_series_status(series_id, "awaiting_review")
        store.finish_run(run_id, "complete")
        run_events.emit(run_id, "done", "Plan ready for review", terminal=True)
    except AgentHardFail as exc:
        store.set_series_status(series_id, "context_ready")
        store.finish_run(run_id, "failed", str(exc))
        run_events.emit(run_id, "error", f"Agent output failed validation after retries: {exc}", terminal=True)
    except Exception as exc:  # surfaced honestly, never swallowed
        store.set_series_status(series_id, "context_ready")
        store.finish_run(run_id, "failed", f"{exc}\n{traceback.format_exc()}")
        run_events.emit(run_id, "error", f"Pipeline error: {exc}", terminal=True)


def regenerate_concept(series_id: str, concept_id: str, user_feedback: str, run_id: str) -> None:
    """Per-concept regenerate with user feedback: refine (that concept only)
    → re-judge → re-options if qualified."""
    try:
        series = store.get_series(series_id)
        stored = store.get_plan(series_id)
        if not series or not stored:
            raise RuntimeError("series/plan not found")
        context = CreatorContext.model_validate(series["context"])
        family = objective_family(context.objective)
        plan = Plan.model_validate(stored["plan"])
        feedback = Feedback.model_validate(stored["feedback"])
        options = OptionsOutput.model_validate(stored["options"] or {"concept_options": []})
        flagged = {concept_id}
        payload = {"context": context.model_dump(mode="json")}
        rag.ensure_ready()
        # Citation allowlist for this run, seeded with the stored plan's ids:
        # a refine agent may legitimately keep citations that already passed
        # both checks when authored — anything beyond those + what it
        # retrieves NOW is an invented citation.
        from app.agents.runner import _cited_ids

        run_retrieved: set[str] = _cited_ids(plan) | _cited_ids(feedback)

        run_events.emit(run_id, "refine", f"Regenerating {concept_id} with your feedback…")
        plan_new, _ = run_agent(
            agent="planner.refine",
            prompt_name="planner",
            model=config.PLANNER_MODEL,
            user_payload={
                **payload,
                "pass": "refine",
                "plan": plan.model_dump(mode="json"),
                "feedback": feedback.model_dump(mode="json"),
                "flagged_concept_ids": [concept_id],
                "user_feedback": user_feedback,
            },
            schema=Plan,
            dispatcher=_dispatcher(run_retrieved, context=context),
            validate=lambda p: validate_plan(
                p, context, rag, previous_plan=plan, flagged_concept_ids=flagged,
                retrieved_ids=run_retrieved,
            ),
            mock_fn=mock_agents.mock_planner_refine,
        )

        run_events.emit(run_id, "critique", "Feedback agent re-judging the concept…")
        fb_new, _ = run_agent(
            agent="feedback.rejudge",
            prompt_name="feedback",
            model=config.FEEDBACK_MODEL,
            user_payload={
                **payload,
                "plan": plan_new.model_dump(mode="json"),
                "only_concept_ids": [concept_id],
                "refined": True,
            },
            schema=Feedback,
            dispatcher=_dispatcher(run_retrieved),
            validate=lambda f: validate_feedback(
                f, _subset_plan(plan_new, flagged), context, rag, retrieved_ids=run_retrieved
            ),
            prompt_replacements={"dynamic_persona": _persona_for(context)},
            mock_fn=mock_agents.mock_feedback,
        )
        feedback = _merge_feedback(feedback, fb_new)
        statuses = _statuses(family, feedback)

        state = statuses.get(concept_id)
        if state and state["status"] in ("qualified", "strong"):
            run_events.emit(run_id, "options", "Regenerating 3 options…")
            concept = next(c for c in plan_new.concepts if c.id == concept_id)
            opts_new, _ = run_agent(
                agent="planner.options",
                prompt_name="planner",
                model=config.PLANNER_MODEL,
                user_payload={
                    **payload,
                    "pass": "options",
                    "concepts": [{**concept.model_dump(mode="json"), "ccs_final": state["ccs"]}],
                },
                schema=OptionsOutput,
                dispatcher=_dispatcher(run_retrieved),
                validate=lambda o: validate_options(o, {concept_id}),
                mock_fn=mock_agents.mock_planner_options,
            )
            merged = [co for co in options.concept_options if co.concept_id != concept_id]
            merged.extend(opts_new.concept_options)
            options = OptionsOutput(concept_options=merged)
        else:
            options = OptionsOutput(
                concept_options=[co for co in options.concept_options if co.concept_id != concept_id]
            )

        store.bump_regen(series_id, concept_id)
        _persist(series_id, plan_new, feedback, options, statuses, keep_order=True)
        store.finish_run(run_id, "complete")
        run_events.emit(run_id, "done", "Concept regenerated", terminal=True)
    except AgentHardFail as exc:
        store.finish_run(run_id, "failed", str(exc))
        run_events.emit(run_id, "error", f"Agent output failed validation after retries: {exc}", terminal=True)
    except Exception as exc:
        store.finish_run(run_id, "failed", f"{exc}\n{traceback.format_exc()}")
        run_events.emit(run_id, "error", f"Pipeline error: {exc}", terminal=True)


# ------------------------------------------------------------------ helpers


def _flagged_ids(family: ObjectiveFamily, feedback: Feedback) -> set[str]:
    """Concepts the refine pass may touch: kill-flagged or below the qualify gate."""
    flagged: set[str] = set()
    for verdict in feedback.concept_verdicts:
        ccs = ccs_mod.compute_ccs(family, ccs_mod.final_ratings_of(verdict))
        if verdict.kill_flags or ccs <= ccs_mod.QUALIFY_THRESHOLD or ccs_mod.hook_gate_failed(verdict):
            flagged.add(verdict.concept_id)
    return flagged


def _statuses(family: ObjectiveFamily, feedback: Feedback) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for verdict in feedback.concept_verdicts:
        ccs = ccs_mod.compute_ccs(family, ccs_mod.final_ratings_of(verdict))
        verdict.ccs_final = ccs  # server value wins
        out[verdict.concept_id] = {"status": ccs_mod.concept_status(family, verdict), "ccs": ccs}
    return out


def _subset_plan(plan: Plan, ids: set[str]) -> Plan:
    return Plan(
        series=plan.series,
        concepts=[c for c in plan.concepts if c.id in ids],
        changes=plan.changes,
    )


def _merge_feedback(base: Feedback, update: Feedback) -> Feedback:
    updated = {v.concept_id: v for v in update.concept_verdicts}
    merged = [updated.get(v.concept_id, v) for v in base.concept_verdicts]
    known = {v.concept_id for v in base.concept_verdicts}
    merged.extend(v for cid, v in updated.items() if cid not in known)
    return Feedback(concept_verdicts=merged)


def _persist(
    series_id: str,
    plan: Plan,
    feedback: Feedback,
    options: OptionsOutput,
    statuses: dict[str, dict[str, Any]],
    keep_order: bool = False,
) -> None:
    existing = {s["concept_id"]: s for s in store.get_concept_states(series_id)}
    store.save_plan(
        series_id,
        plan.model_dump(mode="json"),
        feedback.model_dump(mode="json"),
        options.model_dump(mode="json"),
    )
    for idx, concept in enumerate(plan.concepts):
        state = statuses.get(concept.id, {"status": "rework", "ccs": 0})
        prev = existing.get(concept.id)
        order_idx = prev["order_idx"] if (keep_order and prev) else idx
        store.upsert_concept_state(
            series_id, concept.id, state["status"], state["ccs"], order_idx,
            coverage=state.get("coverage"),
        )
        if prev and prev.get("approved") and not keep_order:
            store.set_concept_approved(series_id, concept.id, True)


def start_plan_run(series_id: str) -> str:
    run_id = store.create_run(series_id, "generate_plan")
    thread = threading.Thread(target=generate_plan, args=(series_id, run_id), daemon=True)
    thread.start()
    return run_id


def start_regen_run(series_id: str, concept_id: str, user_feedback: str) -> str:
    run_id = store.create_run(series_id, "regenerate_concept")
    thread = threading.Thread(
        target=regenerate_concept, args=(series_id, concept_id, user_feedback, run_id), daemon=True
    )
    thread.start()
    return run_id
