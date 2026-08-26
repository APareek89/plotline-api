"""Thread plumbing shared by every driver — envelope, locks, labeled steps,
per-thread workspace, retrieval dispatcher, feedback merge, cost units.

This module exists so the Marketing Studio driver (app/campaign.py) does not
import from the drivers it replaces. Before this, campaign.py reached into
app/thread.py, app/creative.py and app/orchestrator.py for helpers that were
never Content-Studio- or Creative-Studio-specific — which meant the old
drivers could not be deleted without breaking the new one.

Nothing here knows about a product surface. The old drivers re-export these
names so they keep working until they are removed; when they go, this module
is the only definition and nothing else moves.
"""
from __future__ import annotations

import threading
from typing import Any, Optional

from app import ccs as ccs_mod
from app import store
from app.rag_client import rag
from app.schemas import (
    AgentMessage,
    AgentQuestion,
    ArtifactEnvelope,
    CreatorContext,
    Feedback,
    ObjectiveFamily,
    QuestionOption,
)
from app.tools import ToolDispatcher

# --------------------------------------------------------------- cost units --

# One credit = $0.10. The Ad Card reports credits; store.campaign_spend()
# reports USD. Both must reconcile exactly — see the Ad Card acceptance check.
CREDIT_USD = 0.10


def _asset_url(asset_id: str) -> str:
    return f"/api/assets/{asset_id}/file"


# ------------------------------------------------- labeled steps and locks --

# thread_id → current labeled step. An invariant, not a nicety: the UI shows
# this instead of a bare spinner. In-memory (single-process dev server).
_working: dict[str, str] = {}
_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def working_step(thread_id: str) -> Optional[str]:
    return _working.get(thread_id)


def _thread_lock(thread_id: str) -> threading.Lock:
    with _lock_guard:
        return _locks.setdefault(thread_id, threading.Lock())


# ----------------------------------------------------------------- envelope --


def _say(
    thread_id: str,
    text: str,
    artifacts: Optional[list[ArtifactEnvelope]] = None,
    question: Optional[Any] = None,
    *,
    options: Optional[list[dict[str, Any]]] = None,
    note: Optional[str] = None,
    multi: bool = False,
    free_text: bool = True,
) -> None:
    """Post an agent turn.

    `question` accepts a plain string or a ready AgentQuestion. The string form
    is kept because most call sites ask an open question with no fixed answers,
    and forcing every one of them to construct an object would add ceremony
    without adding truth. Pass `options=[{label, event, ...}]` to attach the
    tappable CTAs — those are what the composer renders above itself.
    """
    q: Optional[AgentQuestion] = None
    if isinstance(question, AgentQuestion):
        q = question
    elif question:
        # A question is a prompt to the user, not a place to dump content. Long
        # text here is always a bug upstream; truncating keeps the turn valid
        # instead of failing validation deep in the driver.
        text_q = str(question)
        if len(text_q) > 240:
            text_q = text_q[:237].rstrip() + "…"
        q = AgentQuestion(
            text=text_q,
            options=[QuestionOption(**o) for o in (options or [])],
            multi=multi, free_text=free_text, note=note,
        )
    if q is not None and not q.options and artifacts:
        q.options = _options_from(artifacts)
    msg = AgentMessage(thread_id=thread_id, text=text, artifacts=artifacts or [], question=q)
    store.append_message(thread_id, "agent", msg.model_dump(mode="json"))


def _unpack(artifact: Any) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(artifact, ArtifactEnvelope):
        return artifact.id, [a.model_dump() for a in artifact.actions]
    if isinstance(artifact, dict):
        return artifact.get("id", ""), list(artifact.get("actions") or [])
    return "", []


def _options_from(artifacts: list[Any]) -> list[QuestionOption]:
    """Lift EVERY actioned artifact's actions into the question's options.

    Since 2026-08-26 the artifact panel is READ-ONLY and every CTA is answered
    in the chat. The actions are still declared exactly once — on the artifact
    that owns them — and lifted here, so the card and the composer cannot
    disagree about what the user is allowed to do.

    Every artifact in the turn, not just the last one. The options turn posts
    THREE campaign_option cards, each declaring its own approve/regenerate;
    lifting only the last one left options 1 and 2 with no way to approve them
    — a dead end reachable in the browser while every API test passed, which is
    precisely the bug class this lift exists to prevent.

    When more than one card is in play the labels carry the artifact id, because
    three buttons all reading "Approve" cannot tell you what you are approving.
    """
    actioned = [(aid, acts) for aid, acts in (_unpack(a) for a in artifacts) if acts and aid]
    name_them = len(actioned) > 1
    out: list[QuestionOption] = []
    for aid, actions in actioned:
        for a in actions:
            label, event = a.get("label"), a.get("event")
            if not label or not event:
                continue
            out.append(QuestionOption(
                label=f"{label} {aid}" if name_them else label,
                event=event, artifact_id=aid,
                primary=a.get("style") == "primary",
            ))
    return out


def _actions(*pairs: tuple[str, str, str]) -> list[dict[str, Any]]:
    return [{"id": e, "label": label, "style": style, "event": e} for e, label, style in pairs]


# -------------------------------------------------------- thread workspace --

_WORKSPACES: dict[str, dict[str, Any]] = {}


def _pending(thread_id: str) -> dict[str, Any]:
    """Per-thread in-memory workspace. Driver state riding on the thread row
    is too small for this; the durable record is messages + assets +
    generation_log, and a restart rehydrates from those.

    `prompts` is the slot→text map the prompt-edit route writes into, so an
    edited prompt is used VERBATIM downstream (§08 rule 7). It lives here
    rather than in a driver because both drivers and the route need it.
    """
    return _WORKSPACES.setdefault(thread_id, {
        "route": None, "script": None, "voice": None,
        "prompts": {}, "assets": {}, "accepted": set(),
        "endcards": [], "endcard": None, "spent": 0.0, "draft_only": False,
        "seam_rerolled": set(),
    })


# ------------------------------------------------------------- retrieval ----


def _dispatcher(
    surfaced_ids: Optional[set[str]] = None,
    context: Optional[CreatorContext] = None,
) -> ToolDispatcher:
    # Addendum-01 §7.3: retrieval hard-filters platform to the context's set.
    platform_filter = list(context.platforms) if context and context.platforms else None
    return ToolDispatcher(
        rag, store.get_profile, surfaced_ids=surfaced_ids, platform_filter=platform_filter
    )


# --------------------------------------------------------- feedback merge ---


def _flagged_ids(family: ObjectiveFamily, feedback: Feedback) -> set[str]:
    """Concepts the refine pass may touch: kill-flagged or below the qualify gate."""
    flagged: set[str] = set()
    for verdict in feedback.concept_verdicts:
        ccs = ccs_mod.compute_ccs(family, ccs_mod.final_ratings_of(verdict))
        if verdict.kill_flags or ccs <= ccs_mod.QUALIFY_THRESHOLD or ccs_mod.hook_gate_failed(verdict):
            flagged.add(verdict.concept_id)
    return flagged


def _merge_feedback(base: Feedback, update: Feedback) -> Feedback:
    """A refine pass returns verdicts for the flagged subset only — fold them
    over the originals without dropping the untouched ones."""
    updated = {v.concept_id: v for v in update.concept_verdicts}
    merged = [updated.get(v.concept_id, v) for v in base.concept_verdicts]
    known = {v.concept_id for v in base.concept_verdicts}
    merged.extend(v for cid, v in updated.items() if cid not in known)
    return Feedback(concept_verdicts=merged)


# ------------------------------------------------------------ niche mapping --


def _niche_of(content_area: str) -> str:
    """Free-text content area → the corpus's niche key. Used for benchmark and
    saturation lookups; unknown areas fall back to the broadest bucket."""
    area = (content_area or "").lower()
    if "skin" in area or "beauty" in area or "serum" in area:
        return "skincare_d2c"
    if "fit" in area or "gym" in area or "protein" in area:
        return "fitness"
    if "food" in area or "thali" in area:
        return "food"
    if "finance" in area or "money" in area:
        return "personal_finance"
    if "fashion" in area or "outfit" in area:
        return "fashion"
    return "ai_tools"
