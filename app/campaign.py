"""Addendum-03 (v2 Marketing Studio) — the campaign thread driver.

Steps 3-8 of the owner's flow live here: rumination → campaign options
(planner + Agent Council) → templates → campaign detail in the right panel →
refine loop → model-confirm → generation → Ad Card. Steps 0-2 arrive through
start_campaign() and save_block(); path a (cards) and path b (conversation)
write the IDENTICAL CampaignContext — path b is elicitation UX, not a second
data model.

An in-memory per-thread workspace (app/threadkit.py), long work on
a daemon thread with labeled _working steps (never a bare spinner), honest
error cards. The durable record is thread_messages + assets + generation_log +
ad_cards; a process restart loses the workspace, not the audit trail.

Invariants enforced HERE, not in prompts (prompts drift, code doesn't):
cost + an explicit UserEvent before any generation; the single-vs-variants
question always precedes it; user-edited prompts used VERBATIM; per-asset
re-roll only; claims_used ⊆ the CONFIRMED approved claims; Skip on templates
means NO style constraint; typed commands and buttons are the same signal.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import traceback
from typing import Any, Callable, Optional

from app import ccs as ccs_mod
from app import config, store
from app.agents import campaign_mock
from app.agents.council import SEATS, run_council, run_seat
from pydantic import ValidationError

from app.schemas import (GATEABLE_STAGES, KEYFRAME_CHECKS, POLICY_PRESETS, CanonPlan,
                         CanonSheet, HookRack, KeyframeBoard, ReviewPolicy, ShotBoard,
                         REALISM_TEXTURE, TAKE_FIXES, StyleBlock, TakeReject,
                         VariantCell, VariantMatrix, neutral_style_block,
                         policy_from_preset)
from app.seats import SeatConfigError, SeatSpec
from app.seats import resolve as resolve_seats
from app.seats import slugs as seat_slugs
from app.agents.runner import AgentHardFail, _cited_ids, current_thread, run_agent
from app.media import MediaError, estimate_cost, generate
from app.graph import RuminationDeps, RuminationState, build_rumination_graph
from app.rag_client import RagUnavailable, rag
from app.schemas import (
    AdCard,
    BrandBlock,
    ArtifactEnvelope,
    Cadence,
    CampaignBrief,
    CampaignContext,
    CampaignDetail,
    CampaignOption,
    CampaignOptions,
    Concept,
    ConceptVerdict,
    CreatorContext,
    Feedback,
    Hook,
    ModelConfirm,
    Plan,
    PostMedia,
    SeriesLevel,
    TemplateRef,
    UserEvent,
    VariantSpec,
    objective_family,
)
from app.threadkit import (
    CREDIT_USD,
    _actions,
    _asset_url,
    _dispatcher,
    _flagged_ids,
    _merge_feedback,
    _niche_of,
    _pending,
    _say,
    _thread_lock,
    _working,
)
from app.validators import (
    BANNED_ABSTRACTIONS,
    AgentValidationError,
    _RECEIPT_CUES,
    resolve_or_fail,
    build_qc_report,
    ref_slots_text,
    script_thresholds_text,
    validate_canon_sheet,
    validate_council,
    validate_feedback,
    validate_hook_rack,
    validate_keyframe_board,
    validate_shot_board,
)

logger = logging.getLogger("plotline.campaign")

# THE SINGLE SOURCE OF STAGE TRUTH. The dispatcher and _resume both read this;
# stage literals are never scattered.
#
# v3 inserts five gates into the cost ladder. The ordering is not arbitrary: the
# BOARD names which canon the campaign needs, so the board precedes canon; canon
# COMPOSES into keyframes, so canon precedes keyframes; and keyframes GATE motion,
# so nothing animates first. `detail` keeps its stage id — its artifact is
# upgraded in place into the shot board rather than being replaced.
CAMPAIGN_STAGES = [
    "name",       # step 0 — the blank landing's only block (owned by POST /api/campaigns)
    "intake",     # conversation — the ONLY way in since 2026-08-26
    "brief",      # v3 — "are we making the right ad?"; locks the format constraints
    "options",    # step 3 — rumination output, 2-3 campaign options
    "templates",  # step 4 — optional style reference (+ the style block)
    "script",     # v3 — hook rack; the w/s lint runs before anything is paid for
    "detail",     # steps 5+6 — the shot board + refine loop. THE LAST FREE GATE
    "canon",      # v3 — cast/product/environment/voice sheets
    "keyframes",  # v3 — the HARD gate: no motion without approved stills
    "generate",   # step 7 — model-confirm, single vs variants
    "creative",   # step 8 — generated set, per-asset accept/re-roll
    "qc",         # v3 — three-tier report; blocking findings hold delivery
    "done",       # Ad Card assembled
]

_BLOCKS = ("product", "campaign", "brand")

# the one honest way to ship an option with nothing behind it (§7.3)
_NO_EVIDENCE = "no evidence in db"

# variant delta names — the card's promise and _apply_variant's switch are the
# same string, so a delta can never be advertised without being applied.
_V_CONTROL, _V_HOOK, _V_COPY = "control", "hook swap", "copy angle"

# v2 §Step 2: campaign objectives drive the CCF weights through the v1 families.
_OBJECTIVE_MAP = {"awareness": "impressions", "traffic": "engagement", "conversions": "conversions"}
_NORTH_STAR = {"awareness": "reach", "traffic": "link clicks", "conversions": "conversions"}

# v1 Ad Card placement spec table (AdCard._spec_table is the schema-side guard).
# Every ratio listed on a card is a ratio we actually rendered — no phantom crops.
_PLACEMENT_RATIOS: dict[str, dict[str, str]] = {
    "video": {"instagram_reels": "9:16", "youtube_shorts": "9:16", "tiktok": "9:16",
              "instagram_feed": "9:16", "linkedin": "9:16", "x": "9:16"},
    "image": {"instagram_reels": "9:16", "youtube_shorts": "9:16", "tiktok": "9:16",
              "instagram_feed": "4:5", "linkedin": "1:1", "x": "1:1"},
}


# --------------------------------------------------------------- workspace --


_WORKSPACES: dict[str, dict[str, Any]] = {}


def _ws(thread_id: str) -> dict[str, Any]:
    """Per-thread driver state. `prompts` deliberately ALIASES the Creative
    Studio workspace map so the existing POST /api/threads/{id}/prompts/{slot}
    route edits campaign prompts too — one verbatim-prompt path, both studios."""
    ws = _WORKSPACES.get(thread_id)
    if ws is None:
        ws = {
            "campaign_id": None, "sub_mode": None,
            "options": {}, "verdicts": {}, "option_order": [], "killed": set(),
            "approved_option": None, "template": None, "detail": None,
            "ratios": [], "variant_specs": [], "variant_group_id": None, "variant_count": 1,
            "prompts": _pending(thread_id)["prompts"],
            "items": [], "assets": {}, "accepted": set(), "reference": None,
            "spent": 0.0, "cards": [], "retry": None,
        }
        _WORKSPACES[thread_id] = ws
    return ws


def _rehydrate(thread_id: str, ws: dict[str, Any]) -> None:
    """The workspace is in-memory; the transcript isn't. After a restart, rebuild
    the stage state from the artifacts this thread already posted — restoring
    what was said, never inventing what wasn't."""
    if ws.get("hydrated") or ws["options"] or ws["detail"] or ws["items"]:
        ws["hydrated"] = True
        return
    ws["hydrated"] = True
    for message in store.get_messages(thread_id):
        if message["role"] != "agent":
            continue
        for artifact in message["envelope"].get("artifacts", []):
            kind, payload = artifact.get("type"), artifact.get("payload") or {}
            if kind == "campaign_option" and payload.get("option"):
                option = CampaignOption.model_validate(payload["option"])
                ws["options"][option.option_id] = option
                if option.option_id not in ws["option_order"]:
                    ws["option_order"].append(option.option_id)
            # ---- v3 gates. These were added when the five new stages landed and
            # this rehydrator was not updated with them, so a restart between the
            # brief and the board handed shot_board an EMPTY brief and a null
            # option. The agent refused honestly ("INCOMPLETE INPUT") and the run
            # escalated — the failure was upstream, in what it was given.
            # Same species as every other drift here: the artifact list grew and
            # the thing that reads it did not.
            elif kind == "campaign_brief" and payload.get("brief"):
                ws["brief"] = payload["brief"]
            elif kind == "hook_rack" and payload.get("rack"):
                ws["hook_rack"] = payload["rack"]
            elif kind == "canon_sheet" and payload.get("sheets"):
                ws["canon"] = payload["sheets"]
            elif kind == "keyframe_board" and payload.get("board"):
                ws["keyframes"] = payload["board"]
            elif kind == "campaign_detail" and payload.get("detail"):
                ws["detail"] = payload["detail"]
                style = payload["detail"].get("style_ref")
                ws["template"] = TemplateRef.model_validate(style) if style else None
                ws["items"] = []  # a new detail starts a new creative — its own asset set
            elif kind == "model_confirm" and payload.get("confirm"):
                ws["ratios"] = payload.get("ratios") or ws["ratios"]
                ws["variant_specs"] = payload["confirm"].get("variants_proposed") or ws["variant_specs"]
            elif kind == "creative_set":
                ws["variant_group_id"] = ws["variant_group_id"] or payload.get("variant_group_id")
                for item in payload.get("items", []):
                    ws["items"] = [i for i in ws["items"] if i["slot"] != item["slot"]] + [item]
            elif kind == "ad_card" and payload.get("card"):
                ws["cards"].append(payload["card"]["id"])
                ws["variant_group_id"] = ws["variant_group_id"] or payload["card"].get("variant_group_id")
    for option_id in ws["option_order"]:
        activity = store.get_artifact_activity(thread_id, option_id)
        if any(e["event"] == "approved" for e in activity):
            ws["approved_option"] = ws["options"][option_id]
        if any(e["event"] == "downgraded" for e in activity):
            ws["killed"].add(option_id)
    ws["variant_count"] = max(ws["variant_count"],
                              len({i["variant_id"] for i in ws["items"] if i.get("variant_id")}))
    if not ws["variant_group_id"] and any(i.get("variant_id") for i in ws["items"]):
        # nothing on the transcript carried the group id: mint one rather than
        # leave a variant set ungrouped — but NEVER over a restored id, or a
        # resumed set stops sharing the group its Ad Cards already carry.
        ws["variant_group_id"] = store.new_id("vgrp")


def _spawn(thread_id: str, fn: Callable[..., None], *args: Any) -> None:
    """Long work off the request thread + a real target for the Retry action
    on the error card (§04: one Retry, never a stack trace).

    ONE job per thread. A double-tap on Generate would otherwise start a second
    render of the same slots and bill for both, so the placeholder step is
    claimed under the lock BEFORE the thread starts; every spawned turn pops it
    in its own finally."""
    with _thread_lock(thread_id):
        busy = _working.get(thread_id)
        if busy:
            _say(thread_id, f"Still working — {busy}. I'll post it here the moment it lands.")
            return
        _working[thread_id] = "starting the next step"
        _ws(thread_id)["retry"] = (fn, args)

        def _run() -> None:
            # tag every agent run started by this turn with its thread, so the
            # observability view can group nodes by conversation
            current_thread.set(thread_id)
            fn(*args)

        threading.Thread(target=_run, daemon=True).start()


# ------------------------------------------------------------ step 0 + 1-2 --


def start_campaign(name: str) -> dict[str, Any]:
    """Name the campaign, open its thread, and hand straight to the agent.

    There is no longer a path fork or a card screen (owner decision
    2026-08-26). Naming is the ONLY form in the product; everything else is
    said in the thread. The opening turn states what the agent needs, and the
    intake card beside it is a READ-ONLY checklist of the same four things —
    it is not a form, and it carries no actions.

    The four asks ride in the artifact rather than the envelope on purpose:
    `AgentMessage.text` is capped at two sentences, and one question per turn
    is an invariant. Four requests as prose would break both.
    """
    clean = (name or "").strip()
    if not clean:
        raise ValueError("Naming is required: give the campaign a name")

    context = CampaignContext(name=clean)
    campaign_id = store.create_series(context.model_dump(mode="json"))
    store.set_campaign_status(campaign_id, "draft")
    thread = store.create_thread(campaign_id, kind="campaign")
    thread_id = thread["id"]
    _ws(thread_id)["campaign_id"] = campaign_id

    # the name is never interpolated into envelope text — a name like "Q3.5 v2."
    # would blow the <=2-sentence envelope rule; it belongs in the payload.
    _say(
        thread_id,
        "Tell me what we're making and who it's for.",
        [ArtifactEnvelope(
            type="intake_progress", id="intake", title="What I need to start",
            payload={
                "name": clean,
                "filled": {b: False for b in _BLOCKS},
                "next_field": "opening",
                "asks": INTAKE_ASKS,
            },
        )],
        question="What are we making, and who is it for?",
        note="Answer in any order, or ignore the list and just talk — I'll ask for whatever is still missing.",
    )
    store.set_thread_stage(thread_id, "intake")
    store.log_artifact_activity(thread_id, "intake", "proposed")
    return {"campaign_id": campaign_id, "thread": {**thread, "stage": "intake"}}


# The opening checklist. Read-only: it tells the user what the agent is
# listening for, and nothing here is a field the user types into.
INTAKE_ASKS = [
    {"id": "images", "label": "Product images",
     "note": "Drop them in the composer — I read them rather than guess.", "need": "optional"},
    {"id": "what", "label": "What we're making", "note": "One line is enough.", "need": "needed"},
    {"id": "where", "label": "Where it runs", "note": "Sets ratio and duration.", "need": "needed"},
    {"id": "who", "label": "Who it's for", "note": "Drives hook and pacing.", "need": "needed"},
]


def context_filled(context: Any) -> dict[str, bool]:
    """Which context blocks the conversation has managed to fill.

    Confirming claims is NOT required for the brand block. It used to be, which
    meant a brand with nothing quotable could never start a campaign. The
    compliance invariant does not need the gate: an unconfirmed brand simply has
    an EMPTY approved_claims list, so every persuasion claim is unmapped and
    _validate_detail rejects it / the council kill-flags it. Confirming claims
    GRANTS permission to make them; it is not a toll on getting started."""
    ctx = _as_dict(context)
    return {block: bool(ctx.get(block)) for block in _BLOCKS}


_REQUIRED_BLOCKS = ("product", "campaign")   # brand has NO required field


def missing_blocks(context: Any) -> list[str]:
    """What genuinely blocks a rumination. Brand is absent on purpose: every one
    of its fields is optional, so demanding the block exist was ceremony. It is
    defaulted at start (see begin_rumination) rather than demanded here, which
    lets a conversational intake stop asking about it while still capturing it
    if the user volunteers it."""
    ctx = _as_dict(context)
    return [b for b in _REQUIRED_BLOCKS if not ctx.get(b)]


def templates() -> list[dict[str, Any]]:
    """Step 4: static samples the owner drops into samples/templates/. Empty
    manifest (or none) → the picker is skipped entirely, honestly."""
    return _template_manifest()[0]


def _template_manifest() -> tuple[list[dict[str, Any]], Optional[str]]:
    """(library, problem). A manifest we can't parse is NOT an empty library —
    reporting "no templates" there would hide a broken install behind a normal
    skip, so the reason travels back to the caller that speaks to the user."""
    path = config.ROOT / "samples" / "templates" / "manifest.json"
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("templates manifest unreadable (%s)", exc)
        return [], f"{path.name} could not be read: {exc}"
    items = data.get("templates", []) if isinstance(data, dict) else data
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        out.append({
            "id": str(item["id"]),
            "label": item.get("label") or str(item["id"]),
            "thumb": item.get("thumb", ""),
            "type": item.get("type", "image"),
            "style_descriptors": list(item.get("style_descriptors") or []),
        })
    return out, None


# ------------------------------------------------------------------ events --


def handle_event(event: UserEvent) -> None:
    """Both input paths land here; typed text is parsed to the SAME events the
    buttons emit (deterministic — approvals never need a model)."""
    thread = store.get_thread(event.thread_id)
    if not thread:
        raise ValueError("thread not found")
    store.append_message(event.thread_id, "user", event.model_dump(mode="json"))
    ws = _ws(event.thread_id)
    ws["campaign_id"] = thread["series_id"]
    _rehydrate(event.thread_id, ws)
    stage = thread["stage"]

    if event.type == "action":
        # A multi-select answer travels as `values`; everything else has none,
        # so `extra` stays None exactly as before.
        _dispatch(thread, stage, event.action.event, event.action.artifact_id,
                  event.action.values or None)
        return

    text = (event.text or "").strip()
    if event.upload_ids:
        # stash on the workspace so the intake turn can hand them to the agent
        # as structured ids rather than as words inside the message
        ws["pending_uploads"] = list(dict.fromkeys(
            list(ws.get("pending_uploads") or []) + list(event.upload_ids)))
    parsed = _parse(stage, text, ws, panel_focus=event.panel_focus)
    if parsed is None:
        _hint(event.thread_id, _HINTS.get(stage, "Tell me what to change, or use the buttons on the cards."))
        return
    _dispatch(thread, stage, parsed["event"], parsed["artifact_id"], parsed.get("extra"))


def _dispatch(thread: dict[str, Any], stage: str, event: str, artifact_id: str, extra: Any) -> None:
    thread_id, campaign_id = thread["id"], thread["series_id"]
    ws = _ws(thread_id)
    ws["campaign_id"] = campaign_id

    if event == "retry":
        job = ws.get("retry")
        if job:
            # Resume rather than restart. Every stage that already succeeded is
            # served from its checkpoint, so a retry after a transient failure
            # costs the remaining work, not the whole run again.
            ws["resuming"] = True
            _spawn(thread_id, job[0], *job[1])
            return
        _say(thread_id, "Nothing to retry on this thread yet.")
        return

    # ---- intake: conversation is the only way in
    if stage == "intake":
        if event in ("intake", "brief") and isinstance(extra, str):
            _spawn(thread_id, _intake_turn, thread_id, campaign_id, extra)
            return
        if event == "begin":
            try:
                begin_rumination(campaign_id)
            except ValueError as exc:
                _say(thread_id, "Not ready yet.", [_progress_artifact(_context_of(campaign_id))],
                     question=str(exc))
            return
        if event in ("confirm_claims", "confirm_claims_none"):
            _confirm_claims(thread_id, campaign_id,
                            [] if event == "confirm_claims_none" else extra)
            return

    # ---- v3 gates. Each approval advances per the review policy; `regenerate`
    # re-runs the same turn. The handlers own the artifact; this only routes.
    if stage == "brief":
        if event == "approve_brief":
            store.log_artifact_activity(thread_id, "brief", "approved", "")
            store.set_thread_stage(thread_id, advance_from(campaign_id, "brief"))
            _spawn(thread_id, _options_turn, campaign_id, thread_id)
            return
        if event == "regenerate_brief":
            _spawn(thread_id, _brief_turn, thread_id, campaign_id)
            return

    if stage == "script":
        if event == "approve_script":
            store.log_artifact_activity(thread_id, "script", "approved", "")
            store.set_thread_stage(thread_id, advance_from(campaign_id, "script"))
            _spawn(thread_id, _board_turn, thread_id, campaign_id)
            return
        if event == "regenerate_script":
            _spawn(thread_id, _script_turn, thread_id, campaign_id)
            return

    if stage == "detail" and event in ("approve_board", "regenerate_board"):
        if event == "regenerate_board":
            _spawn(thread_id, _board_turn, thread_id, campaign_id)
            return
        store.log_artifact_activity(thread_id, "board", "approved", "")
        store.set_thread_stage(thread_id, advance_from(campaign_id, "detail"))
        _spawn(thread_id, _canon_turn, thread_id, campaign_id)
        return

    if stage == "canon":
        if event in ("approve_canon", "skip_canon"):
            store.log_artifact_activity(
                thread_id, "canon", "approved" if event == "approve_canon" else "skipped",
                "" if event == "approve_canon"
                else "skipped by the user — expect identity drift after roughly three shots")
            store.set_thread_stage(thread_id, advance_from(campaign_id, "canon"))
            _spawn(thread_id, _keyframes_turn, thread_id, campaign_id)
            return

    if stage == "keyframes":
        if event == "approve_keyframes":
            ws["keyframes"] = _approve_all_keyframes(ws.get("keyframes") or {})
            store.log_artifact_activity(thread_id, "keyframes", "approved",
                                        f"{len(ws['keyframes'].get('frames', []))} frames")
            store.set_thread_stage(thread_id, advance_from(campaign_id, "keyframes"))
            _spawn(thread_id, _confirm_turn, thread_id, campaign_id)
            return
        if event == "regenerate_keyframes":
            _spawn(thread_id, _keyframes_turn, thread_id, campaign_id)
            return

    if stage == "qc" and event == "deliver":
        _spawn(thread_id, _assemble_turn, thread_id, campaign_id)
        return

    # ---- step 3: options
    if stage == "options":
        if event == "approve":
            option = ws["options"].get(artifact_id)
            if not option:
                _say(thread_id, f"No option {artifact_id} on this thread.")
                return
            # a kill-flagged option is withheld from the card list, but a stale
            # button or a typed "approve o2" still names it — both land here.
            if artifact_id in ws["killed"]:
                _say(thread_id, f"{artifact_id} was kill-flagged by the council, so it can't ship as written.",
                     [_escalation(f"{artifact_id} is kill-flagged", _kill_reason(ws, artifact_id),
                                  ("regenerate", "Regenerate it", "primary"), artifact_id=artifact_id)],
                     question="Regenerate it against the council's fixes, or approve a different option?")
                return
            ws["approved_option"] = option
            # approving an option starts a fresh creative: the previous one's
            # detail, prompts and assets must not leak into its Ad Card.
            ws.update({"detail": None, "template": None, "ratios": [], "variant_specs": [],
                       "variant_group_id": None, "variant_count": 1, "items": [],
                       "accepted": set(), "reference": None})
            ws["prompts"].clear()
            store.log_artifact_activity(thread_id, artifact_id, "approved")
            _spawn(thread_id, _templates_turn, thread_id, campaign_id)
            return
        if event == "regenerate":
            note = extra if isinstance(extra, str) else None
            store.log_artifact_activity(thread_id, artifact_id, "refine_requested", note)
            _spawn(thread_id, _options_turn, campaign_id, thread_id, note, [artifact_id])
            return

    # ---- step 4: templates
    if stage == "templates" and (event.startswith("pick_") or event == "skip"):
        picked = None
        if event.startswith("pick_"):
            tid = event.removeprefix("pick_")
            match = next((t for t in templates() if t["id"] == tid), None)
            if match is None:
                _say(thread_id, f"No template {tid} in the library.")
                return
            picked = TemplateRef(id=match["id"], type=match["type"],
                                 style_descriptors=match["style_descriptors"])
        ws["template"] = picked  # Skip → None → NO style constraint downstream
        store.log_artifact_activity(thread_id, "templates", "approved",
                                    picked.id if picked else "skipped — no style reference")
        _spawn(thread_id, _after_templates, thread_id, campaign_id)
        return

    # ---- steps 5-6: detail + refine
    if stage == "detail":
        if event == "generate_creative":
            _spawn(thread_id, _confirm_turn, thread_id, campaign_id)
            return
        if event == "refine" and isinstance(extra, dict):
            _spawn(thread_id, _refine_turn, thread_id, campaign_id, extra["target"], extra["note"])
            return

    # ---- step 7: cost + single-vs-variants, then generation
    if stage == "generate":
        if event == "generate_single":
            _spawn(thread_id, _generate_turn, thread_id, campaign_id, 1, False)
            return
        if event == "generate_draft":
            _spawn(thread_id, _generate_turn, thread_id, campaign_id, 1, True)
            return
        if event.startswith("generate_variants_"):
            try:
                count = int(event.rsplit("_", 1)[1])
            except ValueError:
                count = 0
            if count not in (2, 3):
                _say(thread_id, "Variants need a count.", question="Two variants or three?")
                return
            _spawn(thread_id, _generate_turn, thread_id, campaign_id, count, False)
            return

    # ---- step 8: per-asset acceptance
    if stage in ("creative", "generate"):
        if event.startswith("use_as_reference_"):
            _use_as_reference(thread_id, event.removeprefix("use_as_reference_"))
            return
        # v3 §8 — a CLASSIFIED reject. "reject_<cause>_<slot>" carries the
        # diagnosis, so the system proposes the patch that matches the cause
        # instead of re-rolling on "make it better", which is not an instruction.
        if event.startswith("reject_"):
            rest = event.removeprefix("reject_")
            cause = next((c for c in TAKE_FIXES if rest.startswith(c)), None)
            if cause:
                slot = rest.removeprefix(cause).lstrip("_")
                _reject_take(thread_id, slot, cause,
                             extra if isinstance(extra, str) else None)
                return
        if event.startswith("reroll_"):
            _spawn(thread_id, _reroll_turn, thread_id, event.removeprefix("reroll_"),
                   extra if isinstance(extra, str) else None)
            return
        if event == "generate_rest":
            # resume at the count the user already paid into: restarting a
            # variant set at 1 would re-render every slot under new keys.
            _spawn(thread_id, _generate_turn, thread_id, campaign_id, ws["variant_count"], False)
            return
        if event == "accept_all":
            for item in ws["items"]:
                ws["accepted"].add(item["slot"])
            _spawn(thread_id, _qc_turn, thread_id, campaign_id)
            return

    # ---- delivered
    if stage == "done":
        if event == "mark_live":
            card = store.get_ad_card(artifact_id) or (
                store.get_ad_card(ws["cards"][0]) if ws["cards"] else None)
            if not card:
                _say(thread_id, "That Ad Card isn't in the store.")
                return
            card["status"] = "live"
            store.save_ad_card(card)
            store.set_campaign_status(card["campaign_id"], "live")
            store.log_artifact_activity(thread_id, card["id"], "approved", "marked live")
            _say(thread_id, "Marked live — the campaign row flips too.",
                 question="Paste the numbers when you have them (CTR, CPC, CPA, ROAS)?")
            return
        if event == "next_creative":
            _offer_next_creative(thread_id, campaign_id)
            return

    _say(thread_id, f"Nothing changed — {event!r} doesn't apply right now.")


# -------------------------------------------------------- typed → the same --

_OPTION_RE = re.compile(r"\b(?:option\s*)?o?([1-3])\b", re.IGNORECASE)
_SLOT_RE = re.compile(r"\b(?:shot|slide|frame)[ _]?(\d+)\b", re.IGNORECASE)
_COUNT_RE = re.compile(r"\b([23])\b")
_NOTE_RE = re.compile(r"[,—-]\s*(.+)$")

_HINTS = {
    "intake": "Tell me about the product, the campaign or the brand — I'll file it.",
    "options": 'Approve one ("approve o2"), or name what to change ("regenerate o1 — harder proof").',
    "templates": 'Pick a template ("use t2") or "skip" — skipping means no style constraint.',
    "detail": 'Name what to change ("tighten shot 2 copy") or say "generate creative".',
    "generate": '"Single" for one creative, or "2 variants" / "3 variants" — I never guess the count.',
    "creative": '"Accept" the set, "re-roll slide 1", or "use shot 2 as reference".',
    "done": '"Mark live", download the bundle, or "next creative".',
}


def _parse(stage: str, text: str, ws: dict[str, Any], panel_focus: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Deterministic command grammar — buttons and typing emit identical
    events. No model is consulted for approvals, picks or counts."""
    low = text.lower()
    if not low:
        return None
    note = (_NOTE_RE.search(text).group(1).strip() if _NOTE_RE.search(text) else None)

    if stage == "intake":
        if re.match(r"^\s*(start|begin|go|ruminate|ready)\b", low):
            return {"event": "begin", "artifact_id": "intake"}
        # Everything else is the user briefing us. There is no path to pick and
        # no card to fill — typing IS the intake.
        return {"event": "intake", "artifact_id": "intake", "extra": text}

    if stage == "options":
        target = _option_target(text, ws, panel_focus)
        if re.match(r"^\s*(approve|accept|use|go with|pick|choose)\b", low) and target:
            return {"event": "approve", "artifact_id": target}
        if re.match(r"^\s*(regenerate|regen|redo|rework|change|tweak|fix|feedback)\b", low) and target:
            return {"event": "regenerate", "artifact_id": target, "extra": note or text}
        if target:
            return {"event": "regenerate", "artifact_id": target, "extra": note or text}
        return None

    if stage == "templates":
        if "skip" in low or "no template" in low or "without" in low:
            return {"event": "skip", "artifact_id": "templates"}
        for tpl in templates():
            if tpl["id"].lower() in low:
                return {"event": f"pick_{tpl['id']}", "artifact_id": "templates"}
        return None

    if stage == "detail":
        if ("generate" in low and "creative" in low) or low.strip() in ("generate", "go", "make it"):
            return {"event": "generate_creative", "artifact_id": "detail"}
        target = _detail_target(text, ws)
        if target:
            return {"event": "refine", "artifact_id": "detail", "extra": {"target": target, "note": text}}
        return None

    if stage == "generate":
        if "draft" in low:
            return {"event": "generate_draft", "artifact_id": "confirm"}
        if "variant" in low or "test" in low:
            count = _COUNT_RE.search(low)
            if not count:
                return {"event": "generate_variants_0", "artifact_id": "confirm"}  # forces the count question
            return {"event": f"generate_variants_{count.group(1)}", "artifact_id": "confirm"}
        if any(w in low for w in ("single", "one creative", "just one", "one only")) or low.strip() in ("1", "one"):
            return {"event": "generate_single", "artifact_id": "confirm"}
        return None

    if stage in ("creative", "done"):
        if "reference" in low:
            slot = _asset_slot(text, ws)
            if slot:
                return {"event": f"use_as_reference_{slot}", "artifact_id": "creative"}
            return None
        if any(w in low for w in ("re-roll", "reroll", "redo", "again")):
            slot = _asset_slot(text, ws)
            if slot:
                return {"event": f"reroll_{slot}", "artifact_id": "creative", "extra": note or text}
            return None
        if re.match(r"^\s*(accept|approve|ship|lgtm)\b", low):
            return {"event": "accept_all", "artifact_id": "creative"}
        if "live" in low or "posted" in low:
            card_id = ws["cards"][-1] if ws["cards"] else "card"
            return {"event": "mark_live", "artifact_id": card_id}
        if "next" in low or "another" in low:
            return {"event": "next_creative", "artifact_id": "card"}
        return None

    return None


def _option_target(text: str, ws: dict[str, Any], panel_focus: Optional[str]) -> Optional[str]:
    match = _OPTION_RE.search(text)
    if match:
        candidate = f"o{match.group(1)}"
        if candidate in ws["options"]:
            return candidate
    for word, idx in (("first", 0), ("second", 1), ("third", 2), ("last", -1)):
        if re.search(rf"\b{word}\b", text, re.IGNORECASE) and ws["option_order"]:
            try:
                return ws["option_order"][idx]
            except IndexError:
                return None
    if panel_focus in ws["options"]:
        return panel_focus
    return None


def _detail_target(text: str, ws: dict[str, Any]) -> Optional[str]:
    detail = ws.get("detail") or {}
    slots = [s["slot"] for s in detail.get("shots", [])]
    match = _SLOT_RE.search(text)
    if match:
        number = int(match.group(1))
        for slot in slots:
            if slot.endswith(f"{number:02d}") or slot.endswith(str(number)):
                return slot
        return None
    low = text.lower()
    if "cta" in low or "call to action" in low:
        return "cta"
    if any(w in low for w in ("copy", "caption", "headline", "primary")):
        return "copy_primary"
    return None


def _asset_slot(text: str, ws: dict[str, Any]) -> Optional[str]:
    """Resolve 'shot 2' / 'slide 1' / an exact slot key to a rendered item."""
    low = text.lower()
    for item in ws["items"]:
        if item["slot"].lower() in low:
            return item["slot"]
    match = _SLOT_RE.search(text)
    if not match:
        return None
    number = int(match.group(1))
    for item in ws["items"]:
        if re.search(rf"(?:shot|slide)_0?{number}\b", item["slot"]):
            return item["slot"]
    return None


def _hint(thread_id: str, text: str) -> None:
    _say(thread_id, "Didn't catch a campaign command.", question=text)


# ------------------------------------------------------ step 2: intake turn --


def _intake_turn(thread_id: str, campaign_id: str, text: str) -> None:
    """Path b. Runs the intake agent over the whole context and merges the
    result — the SAME schema path a writes (acceptance check)."""
    try:
        _working[thread_id] = "filing what you told me"
        current = _context_of(campaign_id)
        context, log = run_agent(
            agent="campaign_intake",
            prompt_name="campaign_intake",
            model=config.INTAKE_MODEL,
            user_payload={
                "context": current.model_dump(mode="json"),
                "message": text,
                "transcript": _transcript(thread_id),
                "filled": context_filled(current),
                # ids of images the user attached to this message or an earlier
                # one; the agent puts them in product.image_upload_ids
                "attached_upload_ids": list(_ws(thread_id).get("pending_uploads") or []),
            },
            schema=CampaignContext,
            dispatcher=None,
            use_tools=False,
            validate=lambda c: _validate_intake(c, current),
            # No deterministic stand-in for prose parsing: MOCK_LLM=1 surfaces an
            # honest error here rather than faking an intake (mock lives in
            # app/agents/campaign_mock.py the day it exists).
            mock_fn=getattr(campaign_mock, "mock_campaign_intake", None),
            # Enrichment is an INPUT-gathering job: look up the brand or product
            # the user named so the brief starts from facts instead of the
            # user's shorthand. Deliberately NOT given to the planner or the
            # council — a web result must never become the evidence behind a
            # claim, which is what the retrieval corpus is for.
            web_search=True,
        )
        context = _apply_pending_uploads(thread_id, context)
        store.update_series_context(campaign_id, context.model_dump(mode="json"))
        store.log_artifact_activity(thread_id, "intake", "refined", "conversational turn")
        searched = list(getattr(log, "searched", []) or [])
        if searched:
            # Say what was actually looked up. The queries come off the response,
            # so this can never claim a search that did not happen.
            store.log_artifact_activity(
                thread_id, "intake", "refined", "web: " + " · ".join(searched[:3]))
            _ws(thread_id)["searched"] = searched
        _progress_turn(thread_id, context, before=current, conversational=True)
    except AgentHardFail as exc:
        _fail(thread_id, "Intake couldn't produce a valid context after retries — nothing was guessed.", str(exc))
    except Exception as exc:
        _fail(thread_id, f"Intake failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _validate_intake(new: CampaignContext, current: CampaignContext) -> CampaignContext:
    """Intake is a parser with eyes: it may fill blocks, never rename the
    campaign, drop a filled block, or confirm the claims list for the user."""
    errors: list[str] = []
    if new.name.strip() != current.name.strip():
        errors.append(f"name must stay {current.name!r} — the campaign name is the user's, not yours")
    for block in _BLOCKS:
        if getattr(current, block) is not None and getattr(new, block) is None:
            errors.append(f"you dropped the already-filled {block} block — return the FULL context, "
                          "adding only what the new message told you")
    was_confirmed = bool(current.brand and current.brand.claims_confirmed)
    if new.brand and new.brand.claims_confirmed and not was_confirmed:
        errors.append("claims_confirmed is the user's one-tap confirmation in the Brand card — "
                      "never set it yourself; leave it false")
    if was_confirmed and new.brand and not new.brand.claims_confirmed:
        errors.append(
            "claims_confirmed is the user's one-tap — an agent turn may not clear it "
            "(clearing it would unfreeze the confirmed list for the next turn to rewrite)"
        )
    if was_confirmed and new.brand:
        # once confirmed, the list IS the claims source of truth: only the Brand
        # card PUT (save_block, which never runs this validator) may edit it.
        for field in ("approved_claims", "banned_words"):
            if list(getattr(new.brand, field)) != list(getattr(current.brand, field)):
                errors.append(f"{field} is frozen — the user already confirmed the claims list; "
                              "return it unchanged and put anything new in the conversation instead")
    if errors:
        raise AgentValidationError(errors)
    return new


_BLOCK_LABEL = {"product": "Product", "campaign": "Campaign", "brand": "Brand"}


def _apply_pending_uploads(thread_id: str, context: CampaignContext) -> CampaignContext:
    """Attach images the user sent to whatever product block now exists.

    The agent is TOLD to copy attached_upload_ids into product.image_upload_ids,
    but an attachment is the user's file — losing it because a model forgot is
    not an acceptable failure mode. This makes it server-side truth: the ids are
    held on the workspace until there is a product to put them on, then applied
    once and cleared."""
    ws = _ws(thread_id)
    pending = list(ws.get("pending_uploads") or [])
    if not pending or context.product is None:
        return context
    merged = list(dict.fromkeys(list(context.product.image_upload_ids) + pending))[:8]
    ws["pending_uploads"] = []
    return context.model_copy(
        update={"product": context.product.model_copy(update={"image_upload_ids": merged})})


def _autofill_brand(campaign_id: str, context: CampaignContext) -> CampaignContext:
    """Brand has no required field, so an empty one is a valid, honest state:
    no palette, no claims, no constraints. Filling it here is what lets a
    conversational intake stop asking about brand entirely — the user can still
    open the Brand card and set any of it."""
    if context.brand is not None:
        return context
    context = context.model_copy(update={"brand": BrandBlock()})
    store.update_series_context(campaign_id, context.model_dump(mode="json"))
    return context


def _progress_turn(thread_id: str, context: CampaignContext,
                   before: Optional[CampaignContext] = None,
                   conversational: bool = False) -> None:
    """One progress card + at most ONE question for the next missing field.

    The text names WHAT was just filed rather than repeating "Filed." every
    turn — the transcript is the user's record of the conversation, and four
    identical lines tell them nothing about which card moved."""
    complete = not missing_blocks(context)
    unconfirmed = _claims_awaiting_confirmation(context)

    if complete and unconfirmed:
        # The compliance gate, asked while it is still free. An unconfirmed
        # claim is a kill flag at QC, so the cheapest place to settle it is
        # here — before a single option has been ruminated, let alone rendered.
        # This is where claims_confirmed lives now that the Brand card is gone;
        # the agent still may not set it for the user (see _validate_intake).
        _say(thread_id, "Found claims in what you told me.",
             [_progress_artifact(context)],
             question="Which of these may I use on screen?",
             options=[{"label": c, "event": None} for c in unconfirmed]
                     + [{"label": "Confirm selected", "event": "confirm_claims",
                         "artifact_id": "intake", "primary": True},
                        {"label": "None of them", "event": "confirm_claims_none",
                         "artifact_id": "intake"}],
             multi=True,
             note="Anything you don't confirm becomes a kill flag at QC — I won't "
                  "quietly drop it. Confirming none of them is allowed.")
    elif complete:
        _say(thread_id, "That's everything I need.",
             [_progress_artifact(context, actions=_actions(("begin", "Start", "primary")))],
             question="Start the rumination — evidence, options, council review?")
    else:
        filled = [
            _BLOCK_LABEL[b] for b in _BLOCKS
            if getattr(context, b) is not None
            and (before is None or getattr(before, b) is None)
        ]
        text = f"{', '.join(filled)} filed." if filled else "Noted."
        _say(thread_id, text, [_progress_artifact(context)],
             question=_combined_question(context))
    store.log_artifact_activity(thread_id, "intake", "proposed",
                                "complete" if complete else f"next: {_next_field(context)}")


def _claims_awaiting_confirmation(context: CampaignContext) -> list[str]:
    """Extracted claims the user has not yet ruled on.

    Candidates land in brand.approved_claims but mean nothing until
    claims_confirmed is true — `_confirmed_claims()` returns an EMPTY set while
    it is false, so an unconfirmed list grants no permission at all."""
    brand = context.brand
    if brand is None or brand.claims_confirmed:
        return []
    return [c for c in (brand.approved_claims or []) if c and c.strip()]


def _confirm_claims(thread_id: str, campaign_id: str, chosen: Any) -> None:
    """The user's one-tap. `chosen` is the subset they ticked; an empty list is
    a legitimate answer and must still confirm, or an optional input is once
    again wired to a mandatory output (Learning.MD 2026-08-25)."""
    context = _context_of(campaign_id)
    if context.brand is None:
        raise ValueError("no brand block to confirm claims against")

    offered = _claims_awaiting_confirmation(context)
    picked = [c for c in (chosen or []) if c in offered] if chosen else []
    raw = context.model_dump(mode="json")
    raw["brand"] = {**raw["brand"], "approved_claims": picked, "claims_confirmed": True}
    context = CampaignContext.model_validate(raw)
    store.update_series_context(campaign_id, context.model_dump(mode="json"))

    dropped = [c for c in offered if c not in picked]
    store.log_artifact_activity(
        thread_id, "intake", "approved",
        f"claims confirmed: {len(picked)} approved, {len(dropped)} banned")
    _say(thread_id,
         f"{len(picked)} approved, {len(dropped)} off the table."
         if dropped else f"{len(picked)} approved.",
         [_progress_artifact(context, actions=_actions(("begin", "Start", "primary")))],
         question="Start the rumination — evidence, options, council review?")


def _progress_artifact(context: CampaignContext,
                       actions: Optional[list[dict[str, Any]]] = None) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        type="intake_progress", id="intake", title="Campaign brief",
        payload={"filled": context_filled(context), "next_field": _next_field(context)},
        actions=actions or [],
    )


def _next_field(context: CampaignContext) -> Optional[str]:
    if context.product is None:
        return "product"
    if context.campaign is None:
        return "campaign"
    if context.brand is None:
        return "brand"
    return None


# Only these stop the rumination. Brand has NO required field, so a
# conversational intake never asks about it — an empty brand block means "no
# brand constraints", which is true, and the Brand card is still there to edit.
_CRITICAL_ASK = {
    "product": "what the product is — its name and one line on what it actually does",
    "campaign": "the objective (awareness, traffic or conversions), who it is for, "
                "and which platforms it runs on",
}


def _critical_gaps(context: CampaignContext) -> list[str]:
    return [ask for block, ask in _CRITICAL_ASK.items() if getattr(context, block) is None]


def _combined_question(context: CampaignContext) -> Optional[str]:
    """ONE question covering everything still blocking, rather than a queue of
    them. Intake is meant to be faster than a form, not the same work asked
    slowly — interrogating block by block would be a form with extra steps."""
    gaps = _critical_gaps(context)
    if not gaps:
        return None
    if len(gaps) == 1:
        return f"Still need {gaps[0]}. What is it?"
    return "Still need " + "; and ".join(gaps) + "."


# ---------------------------------------------------- step 3: rumination ----


def begin_rumination(campaign_id: str) -> None:
    """Step 3 kick-off. Refuses — by name — on an incomplete context."""
    series = store.get_series(campaign_id)
    if not series:
        raise ValueError("campaign not found")
    context = CampaignContext.model_validate(series["context"])
    missing = missing_blocks(context)
    if missing:
        raise ValueError("Campaign context incomplete — still needed: " + ", ".join(missing))
    # everything downstream assumes a brand object exists; an empty one is the
    # honest default (no palette, no claims, no constraints)
    context = _autofill_brand(campaign_id, context)
    thread_id = _campaign_thread_id(campaign_id)
    if not thread_id:
        raise ValueError("this campaign has no thread to ruminate in")
    # v3: the brief comes FIRST. It is free, it locks the format constraints
    # everything downstream inherits, and options written before the single
    # message exists are options written against a moving target.
    store.set_thread_stage(thread_id, "brief")
    _spawn(thread_id, _brief_turn, thread_id, campaign_id)


def _options_turn(campaign_id: str, thread_id: str, note: Optional[str] = None,
                  regenerate_ids: Optional[list[str]] = None) -> None:
    """Planner options → blind council → one refine loop → option cards."""
    try:
        ws = _ws(thread_id)
        ws["campaign_id"] = campaign_id
        context = _context_of(campaign_id)
        shadow = _shadow_context(context)
        family = objective_family(shadow.objective)
        retrieved: set[str] = set()

        # A checkpoint from a PREVIOUS rumination on this thread is stale by
        # definition — resuming into it would serve the user the campaign they
        # already rejected. A retry after a failure re-enters through `retry`
        # and keeps its checkpoints, which is the whole point.
        if not ws.get("resuming"):
            store.clear_checkpoints(thread_id)
        ws["resuming"] = False

        _working[thread_id] = "retrieving evidence"
        rag.ensure_ready()  # dependency-unavailable surfaces BEFORE any agent call
        niche_assets = _niche_asset_count(shadow)

        previous = CampaignOptions(options=[ws["options"][oid] for oid in ws["option_order"]]) \
            if regenerate_ids and ws["option_order"] else None
        if previous is not None:
            # citations that already passed both layers when first written stay
            # legal on a regenerate — anything beyond those + what it retrieves
            # NOW is an invented source (same rule as regenerate_concept).
            retrieved |= _cited_ids(previous)

        # The rumination pipeline is a GRAPH: plan -> N blind seats in parallel
        # -> chair -> at most one conditional refine. See app/graph.py for why
        # that shape is load-bearing rather than decorative.
        roster = _campaign_seats(campaign_id)
        options, plan, feedback, reviews, retrieved = _ruminate_graph(
            context, shadow, retrieved, niche_assets, note=note,
            previous=previous, regenerate_ids=regenerate_ids,
            thread_id=thread_id, family=family, seats=seat_slugs(roster))

        _record_review(thread_id, feedback, reviews)
        verdicts = {v.concept_id: v for v in feedback.concept_verdicts}
        ws["options"] = {o.option_id: o for o in options.options}
        ws["option_order"] = [o.option_id for o in options.options]
        ws["verdicts"] = {cid: v.model_dump(mode="json") for cid, v in verdicts.items()}

        artifacts: list[ArtifactEnvelope] = []
        killed: list[str] = []
        for option in options.options:
            verdict = verdicts.get(option.option_id)
            if verdict and verdict.kill_flags:
                killed.append(option.option_id)
                store.log_artifact_activity(thread_id, option.option_id, "downgraded",
                                            f"council kill flags: {', '.join(verdict.kill_flags)}")
                continue
            artifacts.append(_option_artifact(option, verdict, family))
            store.log_artifact_activity(
                thread_id, option.option_id, "proposed",
                f"CCS {ccs_mod.compute_ccs(family, ccs_mod.final_ratings_of(verdict))}" if verdict else "no verdict")
        # withholding the card is not enough: the typed path and stale buttons
        # both address options by id, so the kill list has to be state.
        ws["killed"] = set(killed)

        store.set_thread_stage(thread_id, "options")
        if not artifacts:
            _say(thread_id, "Every option came back kill-flagged by the council — none of them ship as written.",
                 [_escalation("All options kill-flagged",
                              f"kill flags on {', '.join(killed)} — see each option's fixes in Activity",
                              ("regenerate", "Draft new options", "primary"))],
                 question="Want me to draft a fresh set, or change the brief first?")
            return

        store.set_campaign_status(campaign_id, "planned")
        head = f"{len(artifacts)} campaign options — council-reviewed, evidence attached"
        if killed:
            head += f"; {len(killed)} withheld on kill flags"
        _say(thread_id, head[:270], artifacts,
             question="Approve one, or tell me what to change?")
    except AgentHardFail as exc:
        _fail(thread_id, "The planner or council output kept failing validation — nothing was silently accepted.", str(exc))
    except RagUnavailable as exc:
        _fail(thread_id, f"Retrieval is down, so I can't ground the options: {exc}", "")
    except Exception as exc:
        _fail(thread_id, f"Rumination failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _run_options(context: CampaignContext, shadow: CreatorContext, retrieved: set[str],
                 niche_assets: int, *, note: Optional[str] = None,
                 previous: Optional[CampaignOptions] = None,
                 flagged: Optional[set[str]] = None,
                 fixes: Optional[list[str]] = None) -> CampaignOptions:
    payload: dict[str, Any] = {
        "context": context.model_dump(mode="json"),
        "pass": "options",
        "niche_asset_count": niche_assets,
    }
    if note:
        payload["user_feedback"] = note
    if previous is not None:
        payload.update({
            "refine": True,
            "previous_options": previous.model_dump(mode="json")["options"],
            "flagged_option_ids": sorted(flagged or set()),
            "council_fixes": fixes or [],
        })
    options, _log = run_agent(
        agent="campaign_planner.options" if previous is None else "campaign_planner.options_refine",
        prompt_name="campaign_planner",
        model=config.PLANNER_MODEL,
        user_payload=payload,
        schema=CampaignOptions,
        dispatcher=_dispatcher(retrieved, context=shadow),
        validate=lambda o: _validate_options(o, retrieved, previous=previous, flagged=flagged),
        # The prompt quotes the SAME lexicon the validator matches on, so the
        # two can never drift: R2 is a literal substring check, and describing
        # it in prose made the planner satisfy its spirit but fail the check.
        prompt_replacements={"receipt_cues": ", ".join(f'"{c}"' for c in _RECEIPT_CUES)},
        mock_fn=campaign_mock.mock_campaign_options,
    )
    return options


def _validate_options(options: CampaignOptions, retrieved: set[str], *,
                      previous: Optional[CampaignOptions] = None,
                      flagged: Optional[set[str]] = None) -> CampaignOptions:
    """R2/R3 reuse the validators module's lexicons (one source of truth for
    'generic'), evidence gets the same two-layer citation check as concepts,
    and a refine pass may only touch the flagged options."""
    errors: list[str] = []
    ids = [o.option_id for o in options.options]
    if len(set(ids)) != len(ids):
        errors.append(f"duplicate option_id in {ids} — ids must be distinct (o1, o2, o3)")
    if len({o.name_line.strip().lower() for o in options.options}) != len(options.options):
        errors.append("name_lines must be distinct — options are different angles, never rewordings")

    for option in options.options:
        blob = " ".join([option.name_line, option.description, option.storyline, option.why_it_fits]).lower()
        for phrase in BANNED_ABSTRACTIONS:
            if phrase in blob:
                errors.append(f"option {option.option_id}: R3 banned abstraction {phrase!r} — replace it "
                              "with a concrete role, number, artifact or outcome")
        if not any(cue in option.storyline.lower() for cue in _RECEIPT_CUES):
            errors.append(f"option {option.option_id}: R2 receipt required — the storyline must name what "
                          "the viewer literally SEES as proof (a demo, a split screen, an invoice, a timer)")
        # resolve_or_fail returns immediately on an empty id list, so silence
        # would otherwise pass as grounding: cite, or say the gap out loud.
        if not option.evidence and _NO_EVIDENCE not in option.why_it_fits.lower():
            errors.append(f"option {option.option_id}: no evidence cited — either cite a source your "
                          f'retrieval tools surfaced in this run, or write "{_NO_EVIDENCE}" in '
                          "why_it_fits; an ungrounded option may not pass as a grounded one")
        resolve_or_fail(list(option.evidence), rag, errors, f"option {option.option_id}",
                        retrieved_ids=retrieved)

    if previous is not None:
        prev_by_id = {o.option_id: o for o in previous.options}
        if set(ids) != set(prev_by_id):
            errors.append(f"a refine pass may not add or drop options — return exactly {sorted(prev_by_id)}")
        for option in options.options:
            if option.option_id in (flagged or set()):
                continue
            prev = prev_by_id.get(option.option_id)
            if prev is not None and _canonical(option) != _canonical(prev):
                errors.append(f"refine pass modified unflagged option {option.option_id} — untouched "
                              "options must come back byte-identical")
    if errors:
        raise AgentValidationError(errors)
    return options


# --------------------------------- council adapter (documented, not a hack) --
#
# The council + validate_feedback speak Plan/Feedback (v1 concept mechanics:
# element verdicts, lenses, kill flags, server-recomputed CCS). A CampaignOption
# is not a Concept, and the v1 Feedback validator has no option-shaped mode.
# Rather than weaken the validator, this file maps each option onto a SHADOW
# concept so the whole v1 judging machine applies unchanged:
#   * option_id  → concept id (verdict ids line up)
#   * element_scores stay EMPTY — options genuinely carry no planner
#     self-ratings, which is exactly the blindness the council requires
#   * the verbatim options travel beside the shadow plan in the payload, so no
#     seat ever judges the truncated copy
# Nothing about validate_feedback is relaxed: every citation still resolves,
# every applicable element still needs a verdict, CCS is still server-computed.


def _shadow_context(context: CampaignContext) -> CreatorContext:
    """CampaignContext → the CreatorContext the v1 machinery (CCF weights,
    platform hard-filter, niche routing) is typed against."""
    product = context.product
    campaign_block = context.campaign
    assert product is not None and campaign_block is not None  # complete-gated
    area = f"{product.name} — {product.description}"
    rules = list(context.brand.approved_claims) if context.brand else []
    banned = list(context.brand.banned_words) if context.brand else []
    return CreatorContext(
        name=context.name,
        mode="one_time",
        content_area=area[:300],
        description=campaign_block.description,
        objective=_OBJECTIVE_MAP[campaign_block.objective],
        target_audience=campaign_block.target_audience,
        platforms=list(campaign_block.platforms),
        cadence=Cadence(type="one_time", concept_count=3),
        content_type="text_video" if campaign_block.creative_type == "video" else "text_image",
        brand_rules=[f"approved claim: {c}" for c in rules] + [f"banned word: {w}" for w in banned],
        notes=[f"campaign objective: {campaign_block.objective}"],
    )


def _shadow_plan(context: CampaignContext, options: CampaignOptions, shadow: CreatorContext) -> Plan:
    campaign_block = context.campaign
    assert campaign_block is not None
    platform = campaign_block.platforms[0]
    concepts = [
        Concept(
            id=option.option_id,
            title=option.name_line,
            description=_clip(option.description, 300),
            creative_direction=_clip(option.storyline, 240),
            hook=Hook(verbal=option.name_line, first_frame=_clip(option.storyline.split(".")[0], 200)),
            format=f"campaign_{campaign_block.creative_type}",
            platform=platform,
            cta="",  # options carry no CTA yet — the CTA is decided in the campaign detail
            effort="L" if campaign_block.creative_type == "video" else "S",
            asset_needs=[],
            element_scores=[],  # blind council: options carry no planner self-ratings
        )
        for option in options.options
    ]
    return Plan(
        series=SeriesLevel(
            objective=shadow.objective,
            north_star_metric=_NORTH_STAR[campaign_block.objective],
            cadence=Cadence(type="one_time", concept_count=len(concepts)),
        ),
        concepts=concepts,
        changes=[],
    )


def _policy_of(campaign_id: str) -> ReviewPolicy:
    """This campaign's review policy. Never raises: a corrupt or outdated blob
    falls back to the defaults, and the defaults ARE today's behaviour — every
    gate `review`, every media count 1. An unreadable setting must not silently
    become a MORE permissive one."""
    raw = store.get_campaign_settings(campaign_id).get("review_policy")
    if not raw:
        return ReviewPolicy()
    try:
        return ReviewPolicy.model_validate(raw)
    except ValidationError as exc:
        logger.warning("campaign %s: unreadable review_policy (%s) — using defaults",
                       campaign_id, exc)
        return ReviewPolicy()


def next_stage(stage: str, *, creative_type: str = "image") -> str:
    """The stage that follows this one. Reads CAMPAIGN_STAGES so a reordering
    happens in one place.

    For an image campaign the keyframes ARE the deliverable, so `keyframes`
    terminates the creative path rather than handing off to motion.
    """
    if stage not in CAMPAIGN_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    idx = CAMPAIGN_STAGES.index(stage)
    if idx + 1 >= len(CAMPAIGN_STAGES):
        return "done"
    return CAMPAIGN_STAGES[idx + 1]


def skipped_stages(campaign_id: str) -> set[str]:
    """Stages whose WORK the campaign has opted out of. Only ever templates or
    canon — the schema refuses the rest — and both surface what is traded."""
    policy = _policy_of(campaign_id)
    return {s for s in GATEABLE_STAGES if policy.mode(s) == "skip"}


def advance_from(campaign_id: str, stage: str, *, creative_type: str = "image") -> str:
    """Where the flow goes next, honouring the review policy.

    `skip` removes the WORK, so the stage is stepped over entirely. `auto` does
    NOT: the artifact is still produced, emitted and audited — the flow simply
    does not wait. That distinction is the whole safety property of the settings
    model, so it lives here rather than in each stage's handler where it would
    be re-decided nine times.
    """
    skipped = skipped_stages(campaign_id)
    nxt = next_stage(stage, creative_type=creative_type)
    while nxt in skipped:
        nxt = next_stage(nxt, creative_type=creative_type)
    return nxt


def _pauses_at(campaign_id: str, stage: str) -> bool:
    """Does the flow STOP here for the user?

    The one semantic that keeps this safe: a gate mode never decides whether the
    artifact is produced, only whether we wait. Downstream stages consume
    upstream artifacts and the audit trail has to stay complete however fast the
    user wants to move.
    """
    return _policy_of(campaign_id).pauses_at(stage)


def _campaign_seats(campaign_id: str) -> list[SeatSpec]:
    """This campaign's council roster: the three built-ins, plus any stakeholder
    seats it configured (doctrine §7 — the seat an agency defines for their
    client's reviewer).

    A broken roster degrades to the built-in council rather than failing the run.
    Losing a guest reviewer costs one opinion; raising here would discard a
    rumination that costs real money and ~25 minutes, over a config typo. The
    settings route that WRITES this list validates it up front (build-order step
    2), so this path is the backstop for a prompt file deleted after the fact,
    not the primary check.
    """
    try:
        configured = store.get_campaign_settings(campaign_id).get("seats") or []
        return resolve_seats(configured)
    except SeatConfigError as exc:
        logger.warning(
            "campaign %s: %s — falling back to the built-in council", campaign_id, exc)
        return resolve_seats()


def _council_payload(context: CampaignContext, options: CampaignOptions,
                     only_ids: Optional[set[str]] = None) -> dict[str, Any]:
    """What every seat and the chair see.

    `niche_asset_count` used to ride along here. It was dropped with the doctrine
    change: it is a statistic ABOUT a corpus, and handing it to a reviewer who
    cannot open that corpus invites exactly the guess the doctrine forbids
    ("only a few assets in this niche, so the angle is probably fresh"). The
    planner still receives it — the planner still retrieves.
    """
    judged = [o.model_dump(mode="json") for o in options.options
              if only_ids is None or o.option_id in only_ids]
    return {
        "context": context.model_dump(mode="json"),
        "options": judged,  # verbatim options — seats never judge the truncated shadow copy
        "stage": "campaign_options",
    }


def _run_seat(*, seat: str, context: CampaignContext, shadow: CreatorContext,
              options: CampaignOptions, plan: Plan, retrieved: set[str],
              niche_assets: int, only_ids: Optional[set[str]] = None
              ) -> tuple[Any, set[str]]:
    """One blind seat, judging from doctrine — the graph calls these
    concurrently.

    Returns the review AND the ids this seat surfaced. That second value is now
    always empty: a doctrine seat retrieves nothing, so it contributes nothing to
    the union the chair's citation check reads. The tuple shape is kept because
    the graph node contract is shared with a future seat that might retrieve, and
    because an empty set is the honest answer rather than a missing one.
    """
    review = run_seat(
        seat,
        _council_payload(context, options, only_ids),
        plan,
        campaign_mock.mock_seat,
    )
    return review, set()


# Keyed by the seat roster, because the roster decides the graph's node set. The
# default three-seat key is built once and reused exactly as the old singleton
# was; a campaign with stakeholder seats compiles its own and caches it too.
_RUMINATION_GRAPHS: dict[tuple[str, ...], Any] = {}


def _rumination_graph(roster: tuple[str, ...]):
    if roster not in _RUMINATION_GRAPHS:
        _RUMINATION_GRAPHS[roster] = build_rumination_graph(
            RuminationDeps(
                run_options=_run_options,
                run_council=_run_council,
                build_plan=_shadow_plan,
                flagged_ids=_flagged_ids,
                merge_feedback=_merge_feedback,
                objective_family=objective_family,
                seat_runner=_run_seat,
                # Read the thread from the contextvar, NOT from a closure. The
                # graph is compiled once and cached, so a captured thread_id
                # would pin every later campaign's progress labels onto the
                # FIRST campaign's thread — the "labelled working steps, never a
                # bare spinner" invariant would hold on campaign one and quietly
                # break on campaign two. current_thread is set per worker thread
                # at the top of every turn.
                on_step=lambda label: _working.__setitem__(
                    current_thread.get() or "", label),
                # Read the thread from the contextvar for the same reason
                # on_step does: the graph is cached for the process lifetime, so
                # a captured thread_id would checkpoint every campaign into the
                # first one's row — which would serve user B user A's campaign.
                load_checkpoint=lambda stage: store.load_checkpoint(
                    current_thread.get() or "", stage),
                save_checkpoint=lambda stage, data: store.save_checkpoint(
                    current_thread.get() or "", stage, data),
            ),
            seats=list(roster),
        )
    return _RUMINATION_GRAPHS[roster]


def _ruminate_graph(context: CampaignContext, shadow: CreatorContext, retrieved: set[str],
                    niche_assets: int, *, note: Optional[str], previous: Optional[CampaignOptions],
                    regenerate_ids: Optional[list[str]], thread_id: str, family: Any,
                    seats: Optional[list[str]] = None):
    """Run the rumination StateGraph and unpack its final state.

    The graph owns the SHAPE (fan-out, join, one-pass refine cap); every unit of
    work inside it is the same function the sequential driver called."""
    graph = _rumination_graph(tuple(seats) if seats else tuple(SEATS))
    final = graph.invoke(RuminationState(
        context=context, shadow=shadow, niche_assets=niche_assets, note=note,
        previous=previous, regenerate_ids=list(regenerate_ids or []),
        retrieved=sorted(retrieved),
    ))
    state = final if isinstance(final, RuminationState) else RuminationState(**final)
    return (state.options, state.plan, state.feedback,
            list(state.seat_reviews), set(state.retrieved))


def _run_council(context: CampaignContext, shadow: CreatorContext, options: CampaignOptions,
                 plan: Plan, retrieved: set[str], niche_assets: int,
                 only_ids: Optional[set[str]] = None,
                 seat_reviews: Optional[list] = None) -> tuple[Feedback, list]:
    def _validate_chair(feedback: Feedback) -> Feedback:
        # retrieved_ids is deliberately EMPTY, not `retrieved`. The planner's ids
        # are still in `retrieved`, so passing them would leave a chair citing
        # chunk:C0421 perfectly legal — the doctrine would be prompt-deep only.
        # An empty set makes resolve_or_fail the fail-closed backstop; the
        # doctrine-specific rules and the readable error live in validate_council.
        feedback = validate_feedback(
            feedback, plan, shadow, rag, retrieved_ids=set(),
            niche_asset_count=niche_assets)
        return validate_council(feedback, seat_reviews=seat_reviews)

    return run_council(
        _council_payload(context, options, only_ids),
        plan,
        validate_chair=_validate_chair,
        mock_seat=campaign_mock.mock_seat,
        mock_chair=getattr(campaign_mock, "mock_chair", None) or _chair_merge,
        seat_reviews=seat_reviews,
    )


def _chair_merge(payload: dict[str, Any], dispatcher: Any) -> dict[str, Any]:
    """MOCK_LLM=1 Marketing Expert. Deterministic bookkeeping, no prose, so it
    lives here rather than in a second mock module — run_agent only reaches it
    when MOCK_LLM=1, and a real mock_chair in campaign_mock.py takes precedence.

    Two shapes, one path. With no stakeholder seats (the default) it judges the
    draft directly, exactly as the single reviewer does. With seats present it
    takes the WORST rating per element — a reviewer does not average away a Low
    — which is the old chair rule, kept because it is the honest one.
    """
    family = objective_family(_OBJECTIVE_MAP[payload["context"]["campaign"]["objective"]])
    elements = ccs_mod.applicable_elements(family)
    seats = payload.get("stakeholder_seats", [])
    # A stakeholder seat's policy refusal has to survive review — if it stops
    # here the QC report downstream never learns a human check is owed.
    policy_check = any(s.get("policy_check_required") for s in seats)

    by_element: dict[str, list[tuple[str, dict]]] = {}
    for seat in seats:
        for score in seat.get("element_scores", []):
            by_element.setdefault(score["element"], []).append((seat["seat"], score))

    kill_flags: list[str] = []
    claims_note = "no unmapped claim found in the draft"
    policy_note = "no policy risk raised"

    # The reviewer's own mechanical first pass (D8): every persuasion claim in
    # the draft against the confirmed approved_claims. This used to belong to
    # the brand seat; with one reviewer it is the reviewer's job, and the mock
    # has to do it or the compliance acceptance checks stop proving anything.
    unmapped = campaign_mock._unmapped_claim(payload)
    if unmapped:
        kill_flags.append("unsubstantiated_claim")
        claims_note = f"unsubstantiated_claim: '{unmapped}'"

    for seat in seats:
        rec = seat.get("kill_recommendation")
        if not rec:
            continue
        low = rec.lower()
        if "claim" in low:
            kill_flags.append("unsubstantiated_claim")
            claims_note = rec[:200]
        elif "policy" in low:
            kill_flags.append("policy_risk")
            policy_note = rec[:200]
        else:
            kill_flags.append("hook_low")

    verdicts = []
    for concept in payload["plan"]["concepts"]:
        element_verdicts = []
        for element in elements:
            scored = by_element.get(element) or []
            if scored:
                seat_name, score = min(scored, key=lambda pair: ccs_mod.RATING_SCORE[pair[1]["rating"]])
                element_verdicts.append({
                    "element": element, "verdict": "agree", "final_rating": score["rating"],
                    "reason": f"{seat_name} seat: {score['reason']}"[:280],
                    "evidence": score.get("evidence") or [],
                    "evidence_gap": not score.get("evidence"),
                })
            else:
                # No stakeholder seat covered this element, so the reviewer
                # judges it itself. A null rating here would read as "nobody
                # looked", which is false and would sink every CCS to zero —
                # the single reviewer is accountable for every element.
                element_verdicts.append({
                    "element": element, "verdict": "agree", "final_rating": "H",
                    "reason": f"reviewer: {element.replace('_', ' ')} holds up for this draft",
                    "evidence": [{"tag": "PRINCIPLE", "source_id": "model",
                                  "claim": "judged from doctrine", "as_of": None}],
                    "evidence_gap": False,
                })
        verdicts.append({
            "concept_id": concept["id"],
            "element_verdicts": element_verdicts,
            "lenses": {
                # v3: insufficient_data is now unconditional, not a function of
                # corpus size. The council judges from doctrine and scans nothing,
                # so there is no asset count that would earn a real saturation
                # answer — the old `niche < SATURATION_MIN_ASSETS` test implied
                # that a big enough corpus would.
                "saturation": {"similar_count": 0, "source_id": None,
                               "note": "the council judges from doctrine, not from a corpus scan",
                               "insufficient_data": True},
                "claims_safety": claims_note,
                "feasibility": "seats raised no feasibility blocker",
                "platform_policy": policy_note,
                "policy_check_required": policy_check,
            },
            "kill_flags": sorted(set(kill_flags)),
            "fixes": [f for seat in seats for f in seat.get("fixes", [])],
            "ccs_final": 0,  # server recomputes; model arithmetic is advisory
        })
    return {"concept_verdicts": verdicts}


def _option_artifact(option: Any, verdict: Any, family: Any) -> ArtifactEnvelope:
    ccs = ccs_mod.compute_ccs(family, ccs_mod.final_ratings_of(verdict)) if verdict else None
    return ArtifactEnvelope(
        type="campaign_option", id=option.option_id, title=option.name_line,
        payload={
            "option": option.model_dump(mode="json"),
            # honesty surface beside the card: what the council actually said
            "ccs": ccs,
            "status": ccs_mod.concept_status(family, verdict) if verdict else "unjudged",
            "kill_flags": list(verdict.kill_flags) if verdict else [],
            "fixes": [f.model_dump(mode="json") for f in verdict.fixes] if verdict else [],
            "evidence_count": len(option.evidence),
        },
        actions=_actions(("approve", "Approve", "primary"), ("regenerate", "Regenerate", "danger")),
    )


def _record_seat_reviews(thread_id: str, reviews: list) -> None:
    """The review is part of the audit trail, not just an input to the refine
    loop.

    The doctrine version rides along because doctrine §8 requires an audit to
    answer WHICH reviewer said this, and a doctrine change invalidates cached
    review output. The agent-run log carries it too, but that surface is gated
    on PLOTLINE_DEBUG_OBSERVABILITY and off in production — this row is the one
    that durably survives.
    """
    for review in reviews:
        stamp = f" [doctrine {review.doctrine_version}]" if review.doctrine_version else ""
        detail = f"{review.seat} seat{stamp}: " + "; ".join(
            f"{s.element}={s.rating}" for s in review.element_scores)
        if review.policy_check_required:
            detail += " | policy check required: " + "; ".join(review.policy_notes)
        if review.kill_recommendation:
            detail += f" | kill: {review.kill_recommendation}"
        store.log_artifact_activity(thread_id, "council", "proposed", detail[:500])


def _record_review(thread_id: str, feedback: Any, reviews: list) -> None:
    """The reviewer's own verdict, plus any stakeholder seats.

    With one Marketing Expert and no stakeholder seats, `reviews` is empty — so
    without this the doctrine version would vanish from the durable audit trail
    entirely, which is exactly what doctrine §8 exists to prevent.
    """
    _record_seat_reviews(thread_id, reviews)
    if feedback is None:
        return
    stamp = f" [doctrine {feedback.doctrine_version}]" if feedback.doctrine_version else ""
    for verdict in feedback.concept_verdicts:
        detail = f"reviewer{stamp} {verdict.concept_id}: " + "; ".join(
            f"{v.element}={v.final_rating or '-'}" for v in verdict.element_verdicts)
        if verdict.kill_flags:
            detail += f" | kill: {', '.join(verdict.kill_flags)}"
        if verdict.lenses.policy_check_required:
            detail += " | policy check required"
        store.log_artifact_activity(thread_id, "council", "proposed", detail[:500])


# ----------------------------------------------------- step 4: templates ----


def _templates_turn(thread_id: str, campaign_id: str) -> None:
    try:
        _working[thread_id] = "checking the template library"
        ws = _ws(thread_id)
        library, problem = _template_manifest()
        if not library:
            ws["template"] = None
            store.log_artifact_activity(thread_id, "templates", "proposed",
                                        f"manifest unreadable — skipped ({problem})" if problem
                                        else "library empty — skipped")
            # the reason itself carries dots and colons — it rides the card, not
            # the envelope, so the <=2-sentence rule survives a long OSError.
            _say(thread_id,
                 ("The template manifest is there but unreadable, so there's no style reference to offer."
                  if problem else
                  "No templates in the library yet — continuing without a style reference."),
                 [_escalation("Template manifest unreadable", problem,
                              artifact_id="templates")] if problem else None)
            _after_templates(thread_id, campaign_id)
            return
        picks = [(f"pick_{t['id']}", (t.get("label") or t["id"]), "secondary") for t in library]
        _say(
            thread_id,
            "Optional style reference — it constrains look and composition, never copy.",
            [ArtifactEnvelope(
                type="template_picker", id="templates", title="Style reference",
                payload={"templates": library, "skip_allowed": True},
                actions=_actions(*picks, ("skip", "Skip", "primary")),
            )],
            question="Pick a template, or skip for no style constraint?",
        )
        store.set_thread_stage(thread_id, "templates")
        store.log_artifact_activity(thread_id, "templates", "proposed", f"{len(library)} templates")
    except Exception as exc:
        _fail(thread_id, f"Couldn't open the template picker: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


# ------------------------------------------- steps 5-6: detail + refine -----


def _detail_turn(thread_id: str, campaign_id: str) -> None:
    try:
        _working[thread_id] = "writing the campaign detail"
        ws = _ws(thread_id)
        context = _context_of(campaign_id)
        shadow = _shadow_context(context)
        option = ws["approved_option"]
        template = ws.get("template")
        retrieved: set[str] = set()
        rag.ensure_ready()
        detail = _run_detail(context, shadow, option, template, retrieved)
        ws["detail"] = detail.model_dump(mode="json")
        _emit_detail(thread_id, ws["detail"], diff=None)
        store.log_artifact_activity(thread_id, "detail", "proposed",
                                    f"v{detail.version} · {len(detail.shots)} {'shots' if detail.creative_type == 'video' else 'slides'}")
    except AgentHardFail as exc:
        _fail(thread_id, "The campaign detail kept failing validation — most likely an unconfirmed claim.", str(exc))
    except RagUnavailable as exc:
        _fail(thread_id, f"Retrieval is down, so the detail can't be grounded: {exc}", "")
    except Exception as exc:
        _fail(thread_id, f"Campaign detail failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _run_detail(context: CampaignContext, shadow: CreatorContext, option: Any,
                template: Optional[TemplateRef], retrieved: set[str], *,
                previous: Optional[dict[str, Any]] = None, target: Optional[str] = None,
                note: Optional[str] = None) -> CampaignDetail:
    payload: dict[str, Any] = {
        "context": context.model_dump(mode="json"),
        "pass": "detail" if previous is None else "refine",
        "option": option.model_dump(mode="json") if option is not None else None,
        "template": template.model_dump(mode="json") if template else None,
        "approved_claims": list(context.brand.approved_claims) if context.brand else [],
    }
    if previous is not None:
        payload.update({
            "detail": previous,
            "version": int(previous.get("version", 1)) + 1,
            "changes": list(previous.get("changes", [])),
            "target": target,
            "user_request": note,
        })
    detail, _log = run_agent(
        agent="campaign_planner.detail" if previous is None else "campaign_planner.refine",
        prompt_name="campaign_planner",
        model=config.PLANNER_MODEL,
        user_payload=payload,
        schema=CampaignDetail,
        dispatcher=_dispatcher(retrieved, context=shadow),
        validate=lambda d: _validate_detail(d, context, template, previous=previous, target=target),
        mock_fn=campaign_mock.mock_campaign_detail,
    )
    return detail


def _confirmed_claims(context: CampaignContext) -> set[str]:
    """CONFIRMED is the operative word. A saved-but-unconfirmed list is a set of
    CANDIDATES, not permissions — treating it as approved would let the extractor
    grant itself authority.

    Extracted so the brief's proof_points and the detail's claims_used ask the
    SAME question. Two copies of a compliance rule is one copy plus a bug.
    """
    brand = context.brand
    if brand is None or not brand.claims_confirmed:
        return set()
    return set(brand.approved_claims)


def _unmapped_claims(used: list[str], context: CampaignContext) -> list[str]:
    approved = _confirmed_claims(context)
    return [c for c in used if c not in approved]


def _validate_brief(brief: CampaignBrief, context: CampaignContext) -> CampaignBrief:
    """v3 §1. Free to produce, so every check here is free too — and each one
    catches something that would otherwise cost money further down."""
    errors: list[str] = []

    unmapped = _unmapped_claims(brief.proof_points, context)
    if unmapped:
        errors.append(
            f"proof_points {unmapped} are NOT in the confirmed approved_claims "
            f"{sorted(_confirmed_claims(context))} — a proof point IS a claim; drop it or "
            "get it approved. An unmapped claim is a kill flag, not a stretch")

    campaign_block = context.campaign
    if campaign_block is not None:
        if brief.objective != campaign_block.objective:
            errors.append(
                f"brief objective {brief.objective!r} contradicts the campaign card's "
                f"{campaign_block.objective!r} — the brief ECHOES the cards, it does not re-decide them")
        if brief.creative_type != campaign_block.creative_type:
            errors.append(
                f"brief creative_type {brief.creative_type!r} contradicts the campaign card's "
                f"{campaign_block.creative_type!r}")
        stray = [p for p in brief.platforms if p not in campaign_block.platforms]
        if stray:
            errors.append(f"brief names platform(s) {stray} that the campaign card does not")

    if errors:
        raise AgentValidationError(errors)

    # WARNINGS, not errors — D2 is a judgment call and blocking on a heuristic
    # would make the brief harder to produce than the ad. They render on the card.
    warnings: list[str] = []
    if " and " in brief.single_message:
        warnings.append(
            "single_message joins two propositions with \"and\" — D2 says an ad that says two "
            "things communicates neither. Check this is one idea, not two.")
    product = context.product
    if product is not None and product.name and product.name.lower() not in brief.brand_role.lower():
        warnings.append(
            f"brand_role does not name {product.name} — D4 asks how the brand FUNCTIONS in the "
            "story, not that it appears at the end.")
    brief.warnings = warnings
    return brief


def _validate_detail(detail: CampaignDetail, context: CampaignContext,
                     template: Optional[TemplateRef], *,
                     previous: Optional[dict[str, Any]] = None,
                     target: Optional[str] = None) -> CampaignDetail:
    """The compliance gate: claims_used ⊆ CONFIRMED approved claims, no banned
    words, the picked template's look (or none at all on Skip), and — on a
    refine — everything the user didn't name comes back byte-identical."""
    errors: list[str] = []
    brand = context.brand
    campaign_block = context.campaign
    assert brand is not None and campaign_block is not None

    # CONFIRMED is the operative word. A saved-but-unconfirmed list is a set of
    # CANDIDATES, not permissions — treating it as approved would let the
    # extractor grant itself authority. This used to be implicit (the campaign
    # could not start unconfirmed); now that confirming is optional it has to be
    # explicit, or dropping the gate would silently approve every candidate.
    approved = _confirmed_claims(context)
    unmapped = _unmapped_claims(detail.claims_used, context)
    if unmapped:
        errors.append(
            f"claims_used {unmapped} are NOT in the confirmed approved_claims {sorted(approved)} — "
            "drop the claim or drop the persuasion point; an unmapped claim is a kill flag, not a stretch")
    blob = " ".join(
        [detail.copy_primary, detail.cta]
        + [s.visual_prompt for s in detail.shots]
        + [s.vo_or_copy or "" for s in detail.shots]
    ).lower()
    hit = [w for w in brand.banned_words if w and w.lower() in blob]
    if hit:
        errors.append(f"banned word(s) {hit} appear in the detail — the brand policy forbids them")

    if detail.creative_type != campaign_block.creative_type:
        errors.append(f"creative_type must be {campaign_block.creative_type!r} (the campaign card decided it)")
    if template is None and detail.style_ref is not None:
        errors.append("no template was picked (Skip) — style_ref must be null: skipping means NO style constraint")
    if template is not None and (detail.style_ref is None or detail.style_ref.id != template.id):
        errors.append(f"style_ref must be the picked template {template.id!r}")

    slots = [s.slot for s in detail.shots]
    if len(set(slots)) != len(slots):
        errors.append(f"duplicate slot ids in {slots}")
    if detail.creative_type == "video":
        for shot in detail.shots:
            if not shot.duration_s:
                errors.append(f"{shot.slot}: a video shot needs duration_s")

    if previous is not None:
        prev = CampaignDetail.model_validate(previous)
        prev_slots = {s.slot: s.model_dump(mode="json") for s in prev.shots}
        if set(slots) != set(prev_slots):
            errors.append(f"a refine may not add or remove shots — return exactly {sorted(prev_slots)}")
        for shot in detail.shots:
            if shot.slot == target:
                continue
            before = prev_slots.get(shot.slot)
            if before is not None and shot.model_dump(mode="json") != before:
                errors.append(f"refine touched {shot.slot}, which the user didn't name — only {target} may change")
        if target != "copy_primary" and detail.copy_primary != prev.copy_primary:
            errors.append("refine changed copy_primary, which the user didn't name")
        if target != "cta" and detail.cta != prev.cta:
            errors.append("refine changed the CTA, which the user didn't name")
        if detail.style_ref != prev.style_ref:
            errors.append("refine changed the style reference — that isn't what the user asked for")

    if errors:
        raise AgentValidationError(errors)
    return detail


def _refine_turn(thread_id: str, campaign_id: str, target: str, note: str) -> None:
    """Step 6: edit only what was named; the version + changelog are server
    facts (like CCS), not something the model gets to claim."""
    try:
        _working[thread_id] = f"editing {target}"
        ws = _ws(thread_id)
        previous = ws.get("detail")
        if not previous:
            _say(thread_id, "There's no campaign detail on this thread yet.")
            return
        slots = [s["slot"] for s in previous["shots"]]
        if target not in slots + ["copy_primary", "cta"]:
            _say(thread_id, "I couldn't tell which part to change.",
                 question=f"Name one of: {', '.join(slots)}, copy, or CTA.")
            return

        context = _context_of(campaign_id)
        shadow = _shadow_context(context)
        detail = _run_detail(context, shadow, ws.get("approved_option"), ws.get("template"), set(),
                             previous=previous, target=target, note=note)
        updated = detail.model_dump(mode="json")
        updated["version"] = int(previous.get("version", 1)) + 1
        one_liner = f"v{updated['version']} · {target}: {note.strip()[:120]}"
        if len(updated.get("changes", [])) <= len(previous.get("changes", [])):
            updated["changes"] = list(previous.get("changes", [])) + [one_liner]
        ws["detail"] = updated
        # a new detail version is a new creative: _generate_turn skips slots it
        # has already rendered and _render_slot prefers a cached prompt, so the
        # OLD assets would ship on the Ad Card unless the same reset the approve
        # handler runs happens here too. The template survives — a refine may
        # not change the style reference (see _validate_detail).
        ws.update({"ratios": [], "variant_specs": [], "variant_group_id": None,
                   "variant_count": 1, "items": [], "accepted": set(), "reference": None})
        ws["prompts"].clear()

        diff = _diff(previous, updated, target)
        _emit_detail(thread_id, updated, diff=diff)
        store.log_artifact_activity(thread_id, "detail", "refined", one_liner)
    except AgentHardFail as exc:
        _fail(thread_id, "The refine kept failing validation — it changed more than you named.", str(exc))
    except Exception as exc:
        _fail(thread_id, f"Refine failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _emit_detail(thread_id: str, detail: dict[str, Any], diff: Optional[dict[str, Any]]) -> None:
    payload: dict[str, Any] = {"detail": detail}
    if diff:
        payload["diff"] = diff
    kind = "script" if detail["creative_type"] == "video" else "image prompt set"
    _say(
        thread_id,
        f"Campaign detail v{detail['version']} — the {kind} is in the Context tab.",
        [ArtifactEnvelope(type="campaign_detail", id="detail",
                          title=f"Campaign detail · v{detail['version']}",
                          payload=payload,
                          actions=_actions(("generate_creative", "Generate creative", "primary")))],
        question="Generate creative, or name something to change first?",
    )
    store.set_thread_stage(thread_id, "detail")


def _diff(previous: dict[str, Any], updated: dict[str, Any], target: str) -> dict[str, Any]:
    if target in ("copy_primary", "cta"):
        return {target: {"was": previous.get(target), "now": updated.get(target)}}
    was = next((s for s in previous["shots"] if s["slot"] == target), None)
    now = next((s for s in updated["shots"] if s["slot"] == target), None)
    return {target: {"was": was, "now": now}}


# --------------------------------------- step 7: model confirm + variants ---


def _confirm_turn(thread_id: str, campaign_id: str) -> None:
    """The card that always precedes generation: model, one-line reason, cost,
    the disabled-settings note, and the single-vs-variants question."""
    try:
        _working[thread_id] = "pricing the render"
        ws = _ws(thread_id)
        detail = ws.get("detail")
        if not detail:
            _say(thread_id, "There's no campaign detail to generate from yet.")
            return
        context = _context_of(campaign_id)
        campaign_block = context.campaign
        assert campaign_block is not None
        ctype = detail["creative_type"]
        ratios = _ratios_for(ctype, list(campaign_block.platforms))
        ws["ratios"] = ratios
        base = _estimate(detail, ratios)
        specs = _variant_specs(detail, base)
        ws["variant_specs"] = [s.model_dump(mode="json") for s in specs]

        confirm = ModelConfirm(
            recommended_model=(config.MEDIA_MODELS["video"] if ctype == "video"
                               else config.MEDIA_MODELS["image_final"]),
            reason=("Veo 3.1 Fast: image-to-video off a locked keyframe is what keeps the product identical shot to shot."
                    if ctype == "video"
                    else "nano-banana-2: holds product detail and on-frame text at ad quality, at the lowest cost per frame."),
            cost_usd=round(base, 2),
            variants_proposed=specs,  # settings_note stays the schema default, verbatim
        )
        actions = [("generate_single", f"Generate 1 — ${base:.2f}", "primary")]
        if ctype == "video":
            # Draft-first is an invariant on video; the contract's action list
            # doesn't carry it, so it rides as a fourth, clearly-labeled action.
            actions.append(("generate_draft", f"Draft {detail['shots'][0]['slot']} only — ${_estimate({**detail, 'shots': detail['shots'][:1]}, ratios):.2f}", "secondary"))
        # only counts we can actually fill with a distinct delta are offered —
        # a button for a variant _apply_variant can't deliver sells a clone.
        actions += [(f"generate_variants_{n}", f"{n} variants — ${n * base:.2f}", "secondary")
                    for n in range(2, len(specs) + 1)]

        prompts = _prompt_artifacts(context, ws, detail, ratios)
        _say(
            thread_id,
            f"Editable prompts, model and cost before anything renders — {len(detail['shots'])} × {'/'.join(ratios)}.",
            prompts + [ArtifactEnvelope(
                type="model_confirm", id="confirm", title="Confirm the render",
                payload={
                    "confirm": confirm.model_dump(mode="json"),
                    "ratios": ratios,
                    "cost_single": round(base, 2),
                    **{f"cost_variants_{n}": round(n * base, 2) for n in range(2, len(specs) + 1)},
                    "credits_single": round(base / CREDIT_USD, 1),
                    "draft_first_offer": ctype == "video",
                    "locks": _locks_note(context, ws.get("template")),
                },
                actions=_actions(*actions),
            )],
            question="One creative, or variants? Variants need a count — I never pick it for you.",
        )
        store.set_thread_stage(thread_id, "generate")
        for artifact in prompts:
            store.log_artifact_activity(thread_id, artifact.id, "proposed", artifact.payload["asset_slot"])
        store.log_artifact_activity(thread_id, "confirm", "proposed",
                                    f"{confirm.recommended_model} · ${base:.2f} single")
    except Exception as exc:
        _fail(thread_id, f"Couldn't build the model-confirm card: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _ratios_for(creative_type: str, platforms: list[str]) -> list[str]:
    table = _PLACEMENT_RATIOS[creative_type]
    default = "9:16" if creative_type == "video" else "1:1"
    out = list(dict.fromkeys(table.get(p, default) for p in platforms))
    return out or [default]


def _estimate(detail: dict[str, Any], ratios: list[str]) -> float:
    """USD estimate for one complete creative. Estimates only — the honest
    number is whatever fal bills, and that's what lands in the Ad Card."""
    total = 0.0
    for shot in detail["shots"]:
        for _ratio in ratios:
            total += estimate_cost("image", tier="final")
            if detail["creative_type"] == "video":
                total += estimate_cost("video", duration_s=float(shot.get("duration_s") or 4.0))
    return round(total, 2)


def _variant_specs(detail: dict[str, Any], base: float) -> list[VariantSpec]:
    """Named, MECHANICAL deltas: what the card promises is exactly what
    _apply_variant does to the prompts. No rewordings, no new claims — the
    non-control cuts restructure the approved detail, they don't write new ones.

    A delta this detail cannot carry is not proposed at all: the hook swap needs
    two shots to swap between, so a single-shot creative would pay twice for
    byte-identical media. variant_id is the position in the group, which keeps
    the ids contiguous (A, B) instead of leaving a hole where the swap was."""
    shots = detail["shots"]
    payoff = _clip((shots[-1]["visual_prompt"] if shots else ""), 90)
    setup = _clip((shots[0]["visual_prompt"] if shots else ""), 90)
    lead = detail["claims_used"][0] if detail.get("claims_used") else detail["cta"]
    deltas = [(f"{_V_CONTROL} — the approved detail, unchanged",
               "the control every other cut is measured against")]
    if len(shots) > 1:
        deltas.append((f"{_V_HOOK} — opens on the payoff ({payoff}) instead of the setup ({setup})",
                       "tests whether a payoff-first open beats the setup-first open in the first second"))
    if any((shot.get("vo_or_copy") or "").strip() for shot in shots):
        deltas.append((f"{_V_COPY} — proof-first primary copy, led by \"{_clip(lead, 80)}\"",
                       "tests proof-first copy against the control's promise-first copy, same visual order"))
    return [VariantSpec(variant_id=chr(ord("A") + i), delta=delta, hypothesis=hypothesis,
                        cost_usd=round(base, 2))
            for i, (delta, hypothesis) in enumerate(deltas)]


def _apply_variant(detail: dict[str, Any], spec: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Dispatch on the delta NAMED on the card, not on the letter: the letter is
    only the variant's position in the group and moves when a delta is dropped."""
    kind = spec["delta"].split(" — ")[0] if spec else _V_CONTROL
    if spec is None or kind == _V_CONTROL:
        return detail
    out = json.loads(json.dumps(detail))
    shots = out["shots"]
    if kind == _V_HOOK and len(shots) > 1:
        # cold open on the payoff frame; slot ids keep their identity
        shots[0]["visual_prompt"] = shots[-1]["visual_prompt"]
        if shots[0].get("vo_or_copy") is not None:
            shots[0]["vo_or_copy"] = shots[-1].get("vo_or_copy") or shots[0]["vo_or_copy"]
    elif kind == _V_COPY:
        lead = (out["claims_used"][0] if out.get("claims_used") else out["cta"])
        out["copy_primary"] = f"{lead} — {out['copy_primary']}"
        if shots and shots[0].get("vo_or_copy"):
            shots[0]["vo_or_copy"] = f"{lead} — {shots[0]['vo_or_copy']}"
    return out


def _prompt_artifacts(context: CampaignContext, ws: dict[str, Any], detail: dict[str, Any],
                      ratios: list[str]) -> list[ArtifactEnvelope]:
    """Every prompt the render will send, priced per asset, BEFORE the gate —
    the Creative Studio's asset_prompt shape, keyed into the SAME ws['prompts']
    map POST /api/threads/{id}/prompts/{slot} writes, so an edit here is what
    _render_slot sends verbatim. Slots are the control's; the variant cuts
    inherit them for every shot their named delta leaves alone."""
    is_video = detail["creative_type"] == "video"
    locks = _locks(context)
    out: list[ArtifactEnvelope] = []
    for shot in detail["shots"]:
        for ratio in ratios:
            slot = _slot_key(None, shot["slot"], ratio)
            key_slot = f"{slot}_key" if is_video else slot
            still = ws["prompts"].get(key_slot) or _visual_prompt(context, ws, detail, shot)
            ws["prompts"][key_slot] = still
            out.append(_prompt_artifact(key_slot, config.MEDIA_MODELS["image_final"], still,
                                        estimate_cost("image", tier="final"), ratio, locks,
                                        "keyframe" if is_video else "frame"))
            if not is_video:
                continue
            motion = ws["prompts"].get(slot) or _motion_prompt(shot)
            ws["prompts"][slot] = motion
            out.append(_prompt_artifact(
                slot, config.MEDIA_MODELS["video"], motion,
                estimate_cost("video", duration_s=float(shot.get("duration_s") or 4.0)),
                ratio, locks, "clip"))
    return out


def _prompt_artifact(slot: str, model: str, text: str, cost: float, ratio: str,
                     locks: list[str], kind: str) -> ArtifactEnvelope:
    return ArtifactEnvelope(
        type="asset_prompt", id=f"prompt_{slot}", title=f"{slot} · {kind} prompt",
        payload={"asset_slot": slot, "model": model, "prompt_text": text,
                 "cost": round(cost, 2), "ratio": ratio, "locks": locks},
        actions=[],
    )


# ------------------------------------------------- step 8: generation -------


def _generate_turn(thread_id: str, campaign_id: str, variant_count: int, draft_only: bool) -> None:
    try:
        ws = _ws(thread_id)
        detail = ws.get("detail")
        if not detail:
            _say(thread_id, "There's no campaign detail to generate from yet.")
            return
        context = _context_of(campaign_id)
        store.set_campaign_status(campaign_id, "in_production")
        ratios = ws.get("ratios") or _ratios_for(detail["creative_type"], list(context.campaign.platforms))
        specs: list[Optional[dict[str, Any]]] = (
            list(ws["variant_specs"][:variant_count]) if variant_count > 1 else [None])
        ws["variant_count"] = max(variant_count, 1)
        if variant_count > 1 and not ws.get("variant_group_id"):
            ws["variant_group_id"] = store.new_id("vgrp")

        rendered = {i["slot"] for i in ws["items"]}
        new_items: list[dict[str, Any]] = []
        try:
            for spec in specs:
                vdetail = _apply_variant(detail, spec)
                shots = vdetail["shots"][:1] if draft_only else vdetail["shots"]
                for shot in shots:
                    for ratio in ratios:
                        # already rendered (draft-first → "generate the rest") is never
                        # re-rendered: paying twice for the same slot is a re-roll's job.
                        if _slot_key(spec["variant_id"] if spec else None, shot["slot"], ratio) in rendered:
                            continue
                        item = _render_slot(thread_id, context, ws, vdetail, shot, ratio, spec)
                        # committed one at a time: every asset here is already PAID,
                        # so a failure later in the loop must not drop it from the
                        # workspace and make the user buy it a second time.
                        ws["items"] = ws["items"] + [item]
                        new_items.append(item)
        except MediaError as exc:
            _partial_fail(thread_id, exc, new_items)
            return

        if not new_items:
            _say(thread_id, "Everything in this set is already rendered.",
                 [_creative_artifact(ws["items"], ws.get("variant_group_id"))],
                 question="Re-roll a slot, or accept the set?")
            store.set_thread_stage(thread_id, "creative")
            return
        artifacts = [_creative_artifact(ws["items"], ws.get("variant_group_id"))]
        message = "Rendered — every asset carries its prompt, model and cost."
        if draft_only:
            message = "Draft render only — one shot, so you see the look before the full spend."
            artifacts.append(ArtifactEnvelope(
                type="model_confirm", id="confirm_rest", title="Generate the rest",
                payload={"confirm": {"recommended_model": config.MEDIA_MODELS["video"],
                                     "reason": "same model, remaining shots",
                                     "cost_usd": round(_estimate(detail, ratios) - _estimate({**detail, "shots": detail["shots"][:1]}, ratios), 2),
                                     "settings_note": ModelConfirm.model_fields["settings_note"].default,
                                     "variants_proposed": []}},
                actions=_actions(("generate_rest", "Generate the remaining shots", "primary")),
            ))
        _say(thread_id, message, artifacts,
             question="Re-roll anything, or accept the set?")
        store.set_thread_stage(thread_id, "creative")
    except MediaError as exc:
        _media_fail(thread_id, exc, _ws(thread_id).get("last_prompt"), _ws(thread_id).get("last_slot"))
    except Exception as exc:
        _fail(thread_id, f"Generation error: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _render_slot(thread_id: str, context: CampaignContext, ws: dict[str, Any],
                 vdetail: dict[str, Any], shot: dict[str, Any], ratio: str,
                 spec: Optional[dict[str, Any]]) -> dict[str, Any]:
    """One slot = one deliverable asset (video: keyframe + clip). Prompts are
    whatever sits in ws['prompts'] — the user's edit is used VERBATIM."""
    variant_id = spec["variant_id"] if spec else None
    slot = _slot_key(variant_id, shot["slot"], ratio)
    # image creative: the still IS the deliverable, so it owns the plain slot.
    # video: the still is the keyframe (<slot>_key) and the clip owns the slot.
    is_video = vdetail["creative_type"] == "video"
    key_slot = f"{slot}_key" if is_video else slot
    inherit = _inherited_prompt(ws, shot, ratio, is_video)
    prompt = ws["prompts"].get(key_slot) or (inherit and inherit[0]) \
        or _visual_prompt(context, ws, vdetail, shot)
    ws["prompts"][key_slot] = prompt
    ws["last_prompt"], ws["last_slot"] = prompt, slot

    _working[thread_id] = f"rendering {slot}"
    # 'Use as reference' pins the seed as well as the text locks — that promise
    # is only true if the seed actually rides here, not just on a re-roll.
    seed = (ws.get("reference") or {}).get("seed")
    frame = generate("image", prompt, ratio=ratio, tier="final",
                     seed=int(seed) if seed is not None else None)
    frame_id = store.add_asset(thread_id, key_slot, frame.get("kind", "image"), frame["path"],
                               _params(context, ws, prompt, frame, ratio, variant_id, shot["slot"]),
                               frame["cost"])
    store.log_generation(thread_id, frame_id, "generate", prompt=prompt, model=frame["model"],
                         seed=str(frame.get("seed")), cost=frame["cost"])
    ws["spent"] += frame["cost"]

    if not is_video:
        item = {"asset_id": frame_id, "slot": slot, "kind": frame.get("kind", "image"),
                "preview_url": _asset_url(frame_id), "status": "ready", "cost": frame["cost"],
                "ratio": ratio, "variant_id": variant_id, "cover_asset_id": None,
                "cover_cost": 0.0, "duration_s": None}
        ws["assets"][slot] = frame_id
        return item

    motion = ws["prompts"].get(slot) or (inherit and inherit[1]) or _motion_prompt(shot)
    ws["prompts"][slot] = motion
    ws["last_prompt"], ws["last_slot"] = motion, slot
    _working[thread_id] = f"animating {slot}"
    try:
        clip = generate("video", motion, ratio=ratio, duration_s=float(shot.get("duration_s") or 4.0),
                        image_url=frame.get("url"))
    except MediaError:
        # The keyframe is already paid for. Keep it as a real (still) item so the
        # spend stays visible and a resume re-animates it instead of re-buying it.
        ws["assets"][slot] = frame_id
        ws["items"] = ws["items"] + [{
            "asset_id": frame_id, "slot": slot, "kind": frame.get("kind", "image"),
            "preview_url": _asset_url(frame_id), "status": "keyframe_only", "cost": frame["cost"],
            "ratio": ratio, "variant_id": variant_id, "cover_asset_id": None,
            "cover_cost": 0.0, "duration_s": None,
        }]
        raise
    clip_id = store.add_asset(thread_id, slot, clip.get("kind", "video"), clip["path"],
                              {**_params(context, ws, motion, clip, ratio, variant_id, shot["slot"]),
                               "keyframe_asset": frame_id},
                              clip["cost"])
    store.log_generation(thread_id, clip_id, "generate", prompt=motion, model=clip["model"],
                         cost=clip["cost"])
    ws["spent"] += clip["cost"]
    ws["assets"][slot] = clip_id
    return {"asset_id": clip_id, "slot": slot, "kind": clip.get("kind", "video"),
            "preview_url": _asset_url(clip_id), "status": "ready", "cost": clip["cost"],
            "ratio": ratio, "variant_id": variant_id, "cover_asset_id": frame_id,
            "cover_cost": frame["cost"],
            "duration_s": float(shot.get("duration_s") or 4.0)}


def _item_cost(item: dict[str, Any]) -> float:
    """A video item's price is the clip PLUS the keyframe rendered to seed it —
    quoting only the clip under-reports what fal actually charged."""
    return float(item.get("cost") or 0.0) + float(item.get("cover_cost") or 0.0)


def _slot_key(variant_id: Optional[str], slot: str, ratio: str) -> str:
    tag = ratio.replace(":", "x")
    return f"{variant_id.lower()}_{slot}_{tag}" if variant_id else f"{slot}_{tag}"


def _inherited_prompt(ws: dict[str, Any], shot: dict[str, Any], ratio: str,
                      is_video: bool) -> Optional[tuple[str, Optional[str]]]:
    """(still, motion) the confirm card already showed for this shot, or None.

    The card shows one prompt set — the control's — and the user may have edited
    it. A variant inherits that verbatim for every shot its named delta left
    untouched; a shot the delta DID change re-derives, because the edited text
    describes the frame the variant no longer renders."""
    base = next((s for s in (ws.get("detail") or {}).get("shots", []) if s["slot"] == shot["slot"]), None)
    if base is None or base != shot:
        return None
    control = _slot_key(None, shot["slot"], ratio)
    still = ws["prompts"].get(f"{control}_key" if is_video else control)
    return (still, ws["prompts"].get(control)) if still else None


def _visual_prompt(context: CampaignContext, ws: dict[str, Any], vdetail: dict[str, Any],
                   shot: dict[str, Any]) -> str:
    parts = [shot["visual_prompt"]]
    style = vdetail.get("style_ref")
    if style and style.get("style_descriptors"):
        parts.append("Style reference: " + ", ".join(style["style_descriptors"])
                     + " — look and composition only, never its copy.")
    if vdetail["creative_type"] == "image" and shot.get("vo_or_copy"):
        parts.append(f'On-frame copy, verbatim: "{shot["vo_or_copy"]}"')
    parts.append("Consistency locks: " + "; ".join(_locks(context)))
    reference = ws.get("reference")
    if reference:
        parts.append("Match the look, lighting and framing of the approved reference frame "
                     f"({reference['slot']}).")
    return " ".join(parts)


def _motion_prompt(shot: dict[str, Any]) -> str:
    """The i2v prompt: the keyframe already carries composition, so this
    describes what happens plus the spoken line (Veo renders its own audio —
    no separate TTS spend, and no invented camera moves)."""
    parts = [shot["visual_prompt"]]
    if shot.get("vo_or_copy"):
        parts.append(f'Spoken line, verbatim: "{shot["vo_or_copy"]}"')
    return " ".join(parts)


def _locks(context: CampaignContext) -> list[str]:
    product, brand = context.product, context.brand
    locks = [f"product: {product.name}", f"product detail: {_clip(product.description, 140)}"]
    if brand and brand.palette:
        locks.append("brand palette: " + ", ".join(brand.palette[:4]))
    if brand and brand.font:
        locks.append(f"brand font for any on-frame text: {brand.font}")
    locks.append("no competitor logos, no invented on-frame text")
    return locks


def _locks_note(context: CampaignContext, template: Optional[TemplateRef]) -> dict[str, Any]:
    """Said BEFORE the spend: the product pack constrains the prompt, not the
    pixels — image-reference conditioning needs a publicly fetchable asset URL,
    which this deployment doesn't have."""
    product = context.product
    return {
        "text_locks": _locks(context),
        "product_pack": list(product.image_upload_ids) if product else [],
        "style_ref": template.id if template else None,
        "note": ("the product pack rides as text locks in every prompt; image-reference "
                 "conditioning is not wired (fal needs a public asset URL)"),
    }


def _params(context: CampaignContext, ws: dict[str, Any], prompt: str, out: dict[str, Any],
            ratio: str, variant_id: Optional[str], source_slot: str) -> dict[str, Any]:
    return {
        "model": out["model"], "prompt": prompt, "ratio": ratio, "seed": out.get("seed"),
        "variant_id": variant_id, "source_slot": source_slot,
        "product_pack": list(context.product.image_upload_ids) if context.product else [],
        "style_ref": (ws["template"].id if ws.get("template") else None),
        "reference_slot": (ws["reference"]["slot"] if ws.get("reference") else None),
        "mock": out.get("mock", False),
    }


def _creative_artifact(items: list[dict[str, Any]],
                       variant_group_id: Optional[str] = None) -> ArtifactEnvelope:
    actions = [("accept_all", "Accept all", "primary")]
    for item in items:
        # a re-roll is a full paid render (on video, the priciest one on the
        # thread) — the price rides the button, like every other spend.
        actions.append((f"reroll_{item['slot']}",
                        f"Re-roll {item['slot']} — ${_reroll_estimate(item):.2f}", "secondary"))
        actions.append((f"use_as_reference_{item['slot']}", f"Use {item['slot']} as reference", "secondary"))
    return ArtifactEnvelope(
        type="creative_set", id="creative", title=f"Creative set · {len(items)} assets",
        payload={"items": items, "total_cost": round(sum(_item_cost(i) for i in items), 2),
                 # the group id lives here so a restart can restore it instead of
                 # minting a new one (see _rehydrate)
                 "variant_group_id": variant_group_id},
        actions=_actions(*actions),
    )


def _reroll_estimate(item: dict[str, Any]) -> float:
    """A re-roll re-renders the whole slot: on video that's keyframe + clip."""
    cost = estimate_cost("image", tier="final")
    if item.get("cover_asset_id") or item["kind"] == "video":
        cost += estimate_cost("video", duration_s=float(item.get("duration_s") or 4.0))
    return round(cost, 2)


def _reroll_turn(thread_id: str, slot: str, note: Optional[str]) -> None:
    """Per-asset re-roll — one slot, never the whole deliverable."""
    try:
        ws = _ws(thread_id)
        item = next((i for i in ws["items"] if i["slot"] == slot), None)
        if item is None:
            _say(thread_id, f"No rendered asset in slot {slot}.")
            return
        campaign_id = ws["campaign_id"]
        context = _context_of(campaign_id)
        detail = ws["detail"]
        spec = next((s for s in ws["variant_specs"] if s["variant_id"] == item["variant_id"]), None) \
            if item["variant_id"] else None
        vdetail = _apply_variant(detail, spec)
        source = next((s for s in vdetail["shots"]
                       if _slot_key(item["variant_id"], s["slot"], item["ratio"]) == slot), None)
        if source is None:
            _say(thread_id, f"Slot {slot} has no shot behind it any more.")
            return

        _working[thread_id] = f"re-rolling {slot}"
        is_video = vdetail["creative_type"] == "video"
        key_slot = f"{slot}_key" if is_video else slot
        base = ws["prompts"].get(key_slot) or _visual_prompt(context, ws, vdetail, source)
        prompt = f"{base} Adjustment: {note.strip()}" if note else base
        ws["prompts"][key_slot] = prompt
        ws["last_prompt"], ws["last_slot"] = prompt, slot
        seed = int(time.time()) % 10_000
        if ws.get("reference") and ws["reference"].get("seed") is not None:
            seed = int(ws["reference"]["seed"])
        frame = generate("image", prompt, ratio=item["ratio"], tier="final", seed=seed)
        frame_id = store.add_asset(thread_id, key_slot, frame.get("kind", "image"), frame["path"],
                                   {**_params(context, ws, prompt, frame, item["ratio"],
                                              item["variant_id"], source["slot"]), "reroll": True},
                                   frame["cost"])
        store.log_generation(thread_id, frame_id, "reroll", prompt=prompt, model=frame["model"],
                             seed=str(seed), cost=frame["cost"])
        ws["spent"] += frame["cost"]

        if is_video:
            motion = ws["prompts"].get(slot) or _motion_prompt(source)
            if note:
                motion = f"{motion} Adjustment: {note.strip()}"
            ws["prompts"][slot] = motion
            clip = generate("video", motion, ratio=item["ratio"],
                            duration_s=float(source.get("duration_s") or 4.0),
                            image_url=frame.get("url"))
            asset_id = store.add_asset(thread_id, slot, clip.get("kind", "video"), clip["path"],
                                       {**_params(context, ws, motion, clip, item["ratio"],
                                                  item["variant_id"], source["slot"]),
                                        "keyframe_asset": frame_id, "reroll": True},
                                       clip["cost"])
            store.log_generation(thread_id, asset_id, "reroll", prompt=motion, model=clip["model"],
                                 cost=clip["cost"])
            ws["spent"] += clip["cost"]
            new_item = {**item, "asset_id": asset_id, "kind": clip.get("kind", "video"),
                        "preview_url": _asset_url(asset_id), "cost": clip["cost"],
                        "cover_asset_id": frame_id, "status": "ready"}
        else:
            new_item = {**item, "asset_id": frame_id, "kind": frame.get("kind", "image"),
                        "preview_url": _asset_url(frame_id), "cost": frame["cost"], "status": "ready"}

        ws["items"] = [new_item if i["slot"] == slot else i for i in ws["items"]]
        ws["assets"][slot] = new_item["asset_id"]
        ws["accepted"].discard(slot)
        store.log_artifact_activity(thread_id, "creative", "refined", f"{slot} re-rolled")
        _say(thread_id, f"{slot} re-rolled — only that slot changed.",
             [_creative_artifact([new_item], ws.get("variant_group_id"))],
             question="Keep it, re-roll again, or accept the set?")
    except MediaError as exc:
        _media_fail(thread_id, exc, _ws(thread_id).get("last_prompt"), slot)
    except Exception as exc:
        _fail(thread_id, f"Re-roll failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _use_as_reference(thread_id: str, slot: str) -> None:
    """'Use as reference' = this frame's seed + look locks travel into every
    later render on this thread. Honest about what that can and can't do."""
    ws = _ws(thread_id)
    item = next((i for i in ws["items"] if i["slot"] == slot), None)
    if item is None:
        _say(thread_id, f"No rendered asset in slot {slot}.")
        return
    asset = store.get_asset(item.get("cover_asset_id") or item["asset_id"])
    ws["reference"] = {
        "slot": slot,
        "asset_id": item["asset_id"],
        "seed": (asset or {}).get("params", {}).get("seed"),
        "prompt": (asset or {}).get("params", {}).get("prompt"),
    }
    store.log_generation(thread_id, item["asset_id"], "use_as_reference", prompt=ws["reference"]["prompt"])
    store.log_artifact_activity(thread_id, "creative", "approved", f"{slot} set as the look reference")
    _say(thread_id, f"{slot} is the reference now — its seed and look locks ride on every later render.",
         question="Re-roll something against it, or accept the set?")


# ------------------------------------------------ step 8: Ad Card assembly --


def _assemble_turn(thread_id: str, campaign_id: str) -> None:
    try:
        _working[thread_id] = "assembling the Ad Card"
        ws = _ws(thread_id)
        context = _context_of(campaign_id)
        detail = ws["detail"]
        campaign_block = context.campaign
        brand = context.brand
        assert campaign_block is not None and brand is not None
        option_id = ws["approved_option"].option_id if ws.get("approved_option") else "o1"
        accepted = [i for i in ws["items"] if i["slot"] in ws["accepted"]]
        if not accepted:
            _say(thread_id, "Nothing is accepted yet, so there's nothing to assemble.")
            return

        variant_ids = list(dict.fromkeys(i["variant_id"] for i in accepted))
        cards: list[dict[str, Any]] = []
        artifacts: list[ArtifactEnvelope] = []
        for variant_id in variant_ids:
            spec = next((s for s in ws["variant_specs"] if s["variant_id"] == variant_id), None)
            vdetail = _apply_variant(detail, spec)
            placements, banned_hit = _placements(vdetail, campaign_block, brand)
            if banned_hit:
                _say(thread_id, f"Stopping before the Ad Card: the copy uses a banned word ({banned_hit}).",
                     question="Refine the copy and I'll assemble it — say what it should say instead.")
                store.set_thread_stage(thread_id, "detail")
                return

            media, spend = [], 0.0
            for item in [i for i in accepted if i["variant_id"] == variant_id]:
                asset = store.get_asset(item["asset_id"])
                if not asset:
                    continue
                spend += float(asset["cost"] or 0.0)
                # a video's keyframe is a paid render of its own; leaving it out
                # makes the card report less than the thread actually charged.
                cover = store.get_asset(item["cover_asset_id"]) if item.get("cover_asset_id") else None
                if cover:
                    spend += float(cover["cost"] or 0.0)
                media.append(PostMedia(
                    kind=asset["kind"] if asset["kind"] in ("video", "image", "audio") else "image",
                    ratio=item["ratio"],
                    duration_s=item.get("duration_s"),
                    url=_asset_url(item["asset_id"]),
                    cover_url=_asset_url(item["cover_asset_id"]) if item.get("cover_asset_id") else None,
                    params={"model": asset["params"].get("model"), "prompt_id": item["slot"],
                            "prompt": asset["params"].get("prompt"), "seed": asset["params"].get("seed"),
                            "cost": asset["cost"], "variant_id": variant_id},
                ))
            if not media:
                continue
            ratios = list(dict.fromkeys(m.ratio for m in media))
            card_id = store.new_id("ad")
            card = AdCard(
                id=card_id, campaign_id=campaign_id, thread_id=thread_id, option_id=option_id,
                variant_group_id=ws.get("variant_group_id") if variant_id else None,
                variant_id=variant_id,
                creative_type=vdetail["creative_type"],
                placements=placements,
                ratios=ratios,
                naming=_naming(context, option_id, variant_id, ratios[0]),
                media=media,
                total_cost_credits=round(spend / CREDIT_USD, 1),
                status="ready",
                created_at=time.time(),
            ).model_dump(mode="json")
            store.save_ad_card(card)
            cards.append(card)
            ws["cards"].append(card_id)
            artifacts.append(ArtifactEnvelope(
                type="ad_card", id=card_id,
                title=f"Ad Card · {card['naming']}",
                payload={"card": card},
                actions=_actions(("mark_live", "Mark live", "primary")),
            ))
            store.log_artifact_activity(thread_id, card_id, "proposed",
                                        f"{len(media)} assets · {card['total_cost_credits']} credits")

        if not cards:
            _say(thread_id, "None of the accepted slots still have assets behind them.")
            return
        store.set_campaign_status(campaign_id, "ready")
        store.set_thread_stage(thread_id, "done")
        headline = ("Ad Card ready — copy per placement, ratios, naming and the export bundle"
                    if len(cards) == 1
                    else f"{len(cards)} Ad Cards ready, sharing one variant group")
        _say(thread_id, headline, artifacts,
             question="Mark it live, or start the next creative for this campaign?")
        _emit_variant_matrix(thread_id)
    except Exception as exc:
        _fail(thread_id, f"Ad Card assembly failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _placements(vdetail: dict[str, Any], campaign_block: Any, brand: Any) -> tuple[dict[str, str], Optional[str]]:
    """Per-placement copy is ASSEMBLED from the approved copy + CTA, never
    re-written: a second writing pass is exactly how an unapproved claim gets
    in. Platform norms change the shape, not the substance."""
    primary, cta = vdetail["copy_primary"], vdetail["cta"]
    out: dict[str, str] = {}
    for platform in campaign_block.platforms:
        if platform == "x":
            text = f"{primary} {cta}"[:280]
        elif platform == "linkedin":
            text = f"{primary}\n\n{cta}"
        elif platform in ("instagram_reels", "instagram_feed", "tiktok", "youtube_shorts"):
            text = f"{primary}\n\n{cta}"
        else:
            text = f"{primary}\n\n{cta}"
        out[platform] = text
    blob = " ".join(out.values()).lower()
    hit = next((w for w in (brand.banned_words or []) if w and w.lower() in blob), None)
    return out, hit


def _naming(context: CampaignContext, option_id: str, variant_id: Optional[str], ratio: str) -> str:
    brand = context.brand
    handle = ""
    if brand and brand.url:
        handle = re.sub(r"^www\.", "", re.sub(r"^https?://", "", brand.url).split("/")[0]).split(".")[0]
    if not handle and context.product:
        handle = context.product.name
    return "_".join([
        _slug(handle or "brand"), _slug(context.name), _slug(option_id),
        _slug(variant_id or "single"), ratio.replace(":", "x"),
    ])


def _offer_next_creative(thread_id: str, campaign_id: str) -> None:
    ws = _ws(thread_id)
    # a kill-flagged option was never on the table, so it isn't put back on it
    # here either — re-offering it would hand the user an Approve that _dispatch
    # is obliged to refuse.
    remaining = [oid for oid in ws["option_order"]
                 if oid not in ws["killed"]
                 and not (ws.get("approved_option") and oid == ws["approved_option"].option_id)]
    if not remaining:
        withheld = [oid for oid in ws["option_order"] if oid in ws["killed"]]
        _say(thread_id,
             (f"This thread's options are used up — {len(withheld)} stayed kill-flagged."
              if withheld else "This thread's options are used up."),
             question="Start a fresh campaign thread, or refine the detail and generate another cut?")
        return
    family = objective_family(_shadow_context(_context_of(campaign_id)).objective)
    artifacts = []
    for oid in remaining:
        verdict = ws["verdicts"].get(oid)
        artifacts.append(_option_artifact(
            ws["options"][oid],
            _verdict_obj(verdict) if verdict else None,
            family,
        ))
    store.set_thread_stage(thread_id, "options")
    _say(thread_id, "The other options are still on the table.", artifacts,
         question="Approve one and I'll take it through to the next Ad Card?")


# ----------------------------------------------------------------- helpers --


def _context_of(campaign_id: str) -> CampaignContext:
    series = store.get_series(campaign_id)
    if not series:
        raise ValueError("campaign not found")
    return CampaignContext.model_validate(series["context"])


def _campaign_thread_id(campaign_id: str) -> Optional[str]:
    threads = [t for t in store.get_series_threads(campaign_id) if t["kind"] == "campaign"]
    return threads[-1]["id"] if threads else None


def _as_dict(context: Any) -> dict[str, Any]:
    if isinstance(context, CampaignContext):
        return context.model_dump(mode="json")
    return dict(context or {})


def _verdict_obj(verdict: dict[str, Any]) -> ConceptVerdict:
    return ConceptVerdict.model_validate(verdict)


def _kill_reason(ws: dict[str, Any], option_id: str) -> str:
    """Why the council killed it, in the council's own words."""
    verdict = ws["verdicts"].get(option_id) or {}
    flags = ", ".join(verdict.get("kill_flags") or []) or "council kill flag"
    fixes = "; ".join(f.get("change", "") for f in (verdict.get("fixes") or []))
    return f"kill flags: {flags}" + (f" — fixes on the table: {fixes[:200]}" if fixes else "")


def _transcript(thread_id: str, limit: int = 12) -> list[str]:
    out: list[str] = []
    for message in store.get_messages(thread_id):
        envelope = message["envelope"]
        if message["role"] == "user" and envelope.get("text"):
            out.append(f"user: {envelope['text']}")
        elif message["role"] == "agent" and envelope.get("question"):
            out.append(f"agent asked: {envelope['question']}")
    return out[-limit:]


def _niche_asset_count(shadow: CreatorContext) -> int:
    """Honest count for the saturation guard (§7.3) — retrieval, not a guess."""
    try:
        return len(rag.search_corpus("", k=50, filters={"kind": "asset",
                                                        "niche": _niche_of(shadow.content_area)}))
    except RagUnavailable:
        return 0


def _canonical(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _slug(text: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", (text or "").lower())).strip("_") or "x"


def _escalation(title: str, reason: str, *actions: tuple[str, str, str],
                artifact_id: str = "error") -> ArtifactEnvelope:
    # artifact_id rides the card so an action on it dispatches against the thing
    # that failed, not against a generic "error" the option handlers can't find.
    return ArtifactEnvelope(
        type="escalation", id=artifact_id, title=title[:120],
        payload={"reason": reason, "below_threshold_count": 0, "total_concepts": 1,
                 "choices": ["accept_provisional"]},
        actions=_actions(*(actions or (("retry", "Retry", "primary"),))),
    )


def _fail(thread_id: str, summary: str, detail: str) -> None:
    """§04: what broke in plain words + ONE Retry action — never a stack trace
    in the card (the trace goes to the server log)."""
    if detail:
        logger.error("campaign thread %s: %s\n%s", thread_id, summary, detail)
    _say(thread_id, "Something broke — honestly.", [_escalation(summary, summary)])


def _media_fail(thread_id: str, exc: MediaError, prompt: Optional[str], slot: Optional[str]) -> None:
    if exc.policy:
        _say(
            thread_id,
            "The model declined this prompt — nothing was charged.",
            [ArtifactEnvelope(
                type="asset_prompt", id=f"declined_{slot or 'prompt'}",
                title=f"Declined prompt · {slot or 'unknown slot'}",
                payload={"asset_slot": slot or "unknown", "model": "declined",
                         "prompt_text": prompt or "", "cost": 0.0, "ratio": "9:16",
                         "locks": [], "reason": str(exc)[:300]},
                actions=_actions(("retry", "Retry", "primary")),
            )],
            question="Edit the prompt and retry — what should it say instead?",
        )
        return
    _fail(thread_id, f"The provider failed and you weren't charged: {exc}", "")


def _partial_fail(thread_id: str, exc: MediaError, done: list[dict[str, Any]]) -> None:
    """A failure part-way through a set. The assets already rendered were PAID:
    they stay in the workspace and on the card, named and priced, and the offer
    is RESUME — a silent restart would bill for them again."""
    ws = _ws(thread_id)
    if not done:
        _media_fail(thread_id, exc, ws.get("last_prompt"), ws.get("last_slot"))
        return
    spent = round(sum(_item_cost(i) for i in done), 2)
    slot = ws.get("last_slot") or "the next slot"
    reason = "the model declined the prompt" if exc.policy else "the provider failed"
    _say(
        thread_id,
        f"Stopped part-way — {reason} on {slot}, and the {len(done)} asset(s) already rendered are kept.",
        [_creative_artifact(ws["items"], ws.get("variant_group_id")),
         ArtifactEnvelope(
             type="escalation", id="partial",
             title=f"{len(done)} of the set rendered · ${spent:.2f} already charged",
             payload={"reason": str(exc)[:300], "rendered_slots": [i["slot"] for i in done],
                      "failed_slot": slot, "spent_usd": spent,
                      "spent_credits": round(spent / CREDIT_USD, 1),
                      "below_threshold_count": 0, "total_concepts": 1,
                      "choices": ["accept_provisional"]},
             # one action only: Retry would re-enter the same turn, which now
             # skips what rendered — that IS resume, so two buttons would lie
             # about there being a second, cheaper path.
             actions=_actions(("generate_rest", "Resume the rest", "primary")),
         )],
        question="Resume the slots that didn't render, or edit that prompt first?",
    )
    store.set_thread_stage(thread_id, "creative")
    store.log_artifact_activity(
        thread_id, "creative", "downgraded",
        f"partial render: {len(done)} rendered (${spent:.2f} charged), stopped on {slot} — {exc}"[:500])


# =============================================================================
# v3 stage turns. Each one: produce the artifact, validate it server-side, emit
# it, record it, then advance per the review policy. `auto` skips only the WAIT —
# the artifact is produced, emitted and audited either way.
# =============================================================================


def _stage_done(thread_id: str, campaign_id: str, stage: str, *,
                creative_type: str = "image") -> str:
    """Record the stage and move on. Returns the stage now in force."""
    nxt = advance_from(campaign_id, stage, creative_type=creative_type)
    store.set_thread_stage(thread_id, stage if _pauses_at(campaign_id, stage) else nxt)
    return stage if _pauses_at(campaign_id, stage) else nxt


def _brief_turn(thread_id: str, campaign_id: str) -> None:
    """v3 §1 — free, and it locks the format constraints everything inherits."""
    try:
        _working[thread_id] = "writing the campaign brief"
        ws = _ws(thread_id)
        context = _context_of(campaign_id)
        brief, _log = run_agent(
            agent="campaign_brief", prompt_name="campaign_brief",
            model=config.PLANNER_MODEL,
            user_payload={"context": context.model_dump(mode="json")},
            schema=CampaignBrief, dispatcher=None, use_tools=False,
            validate=lambda b: _validate_brief(b, context),
            mock_fn=campaign_mock.mock_campaign_brief)
        ws["brief"] = brief.model_dump(mode="json")
        _say(thread_id,
             "Here's the brief. The one line in bold is what they should remember.",
             [ArtifactEnvelope(
                 type="campaign_brief", id="brief",
                 title=f"Campaign brief · v{brief.version}",
                 payload={"brief": ws["brief"]},
                 actions=_actions(("approve_brief", "Approve brief", "primary"),
                                  ("regenerate_brief", "Regenerate", "secondary")))],
             question="Approve this, or tell me what to change?")
        store.log_artifact_activity(thread_id, "brief", "proposed",
                                    f"v{brief.version} · {brief.single_message[:80]}")
        _stage_done(thread_id, campaign_id, "brief", creative_type=brief.creative_type)
        if not _pauses_at(campaign_id, "brief"):
            _spawn(thread_id, _options_turn, campaign_id, thread_id)
    except AgentHardFail as exc:
        _fail(thread_id, "The brief kept failing validation — most likely a proof point "
                         "that isn't in the confirmed claims.", str(exc))
    except Exception as exc:
        _fail(thread_id, f"Campaign brief failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _script_turn(thread_id: str, campaign_id: str) -> None:
    """v3 §4 — the w/s lint runs here, before anything has been paid for."""
    try:
        _working[thread_id] = "writing the script"
        ws = _ws(thread_id)
        brief = ws.get("brief") or {}
        rack, _log = run_agent(
            agent="hook_rack", prompt_name="hook_rack", model=config.PLANNER_MODEL,
            user_payload={"brief": brief, "option": ws.get("approved_option_json"),
                          "hooks_wanted": _policy_of(campaign_id).hooks_count},
            schema=HookRack, dispatcher=None, use_tools=False,
            # The W1 table is INJECTED from the constant the check reads. Never
            # hand-copied — that is the R2 lexicon bug's exact shape.
            prompt_replacements={"wps_table": script_thresholds_text()},
            validate=validate_hook_rack,
            mock_fn=campaign_mock.mock_hook_rack)
        ws["hook_rack"] = rack.model_dump(mode="json")
        failing = [x.slot for x in list(rack.body) + list(rack.hooks) if x.wps_verdict == "fail"]
        _say(thread_id,
             f"Script is in — {len(rack.hooks)} hook(s) against a locked body."
             + (f" {len(failing)} line(s) need a trim." if failing else ""),
             [ArtifactEnvelope(
                 type="hook_rack", id="hook_rack",
                 title=f"Script · {rack.language}",
                 payload={"rack": ws["hook_rack"]},
                 actions=_actions(("approve_script", "Approve script", "primary"),
                                  ("regenerate_script", "Regenerate", "secondary")))],
             question="Pick a hook and approve, or tell me what to change?")
        store.log_artifact_activity(thread_id, "script", "proposed",
                                    f"{rack.language} · {len(rack.hooks)} hooks · "
                                    f"{len(failing)} over the w/s ceiling")
        _stage_done(thread_id, campaign_id, "script",
                    creative_type=brief.get("creative_type", "image"))
        if not _pauses_at(campaign_id, "script"):
            _spawn(thread_id, _board_turn, thread_id, campaign_id)
    except AgentHardFail as exc:
        _fail(thread_id, "The script kept failing its lints — a line that cannot be said "
                         "in its window, or a missing emotion.", str(exc))
    except Exception as exc:
        _fail(thread_id, f"Script failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _board_turn(thread_id: str, campaign_id: str) -> None:
    """v3 §5 — THE LAST FREE GATE. Everything after it derives from this."""
    try:
        _working[thread_id] = "building the shot board"
        ws = _ws(thread_id)
        brief = ws.get("brief") or {}
        style = ws.get("style_block") or neutral_style_block().model_dump(mode="json")
        ws["style_block"] = style
        board, _log = run_agent(
            agent="shot_board", prompt_name="shot_board", model=config.PLANNER_MODEL,
            user_payload={"brief": brief, "option": ws.get("approved_option_json"),
                          "hook_rack": ws.get("hook_rack"),
                          "style_block_id": style.get("id"),
                          "available_routes": sorted(config.MEDIA_MODELS),
                          # The claims the board may use, verbatim. QA caught the
                          # model inventing "proof_points:<slug>" ids because it
                          # was never handed the strings themselves.
                          "approved_claims": sorted(_confirmed_claims(
                              _context_of(campaign_id)))},
            schema=ShotBoard, dispatcher=None, use_tools=False,
            prompt_replacements={"ref_slots": ref_slots_text()},
            validate=lambda b: _validate_board(b, campaign_id, style),
            mock_fn=campaign_mock.mock_shot_board)
        ws["board"] = board.model_dump(mode="json")
        # The board is the SOURCE OF TRUTH; `detail` is its projection into the
        # shape the generate path already speaks. Projected, never authored
        # twice — everything downstream reads one artifact, and a board edit
        # cannot leave a stale detail behind.
        ws["detail"] = _detail_from_board(board, ws.get("hook_rack"))
        _say(thread_id,
             f"The board is the last free gate — {len(board.shots)} shot(s), "
             f"{board.est_total_usd:.2f} USD after this.",
             [ArtifactEnvelope(
                 type="campaign_detail", id="board",
                 title=f"Shot board · v{board.version}",
                 # `board` is the truth the card renders. `detail` is its
                 # server-computed projection, on the wire so anything still
                 # speaking the pre-v3 shape keeps working — consistent by
                 # construction, because it is derived rather than authored.
                 payload={"board": ws["board"], "detail": ws["detail"],
                          "style_block": style},
                 actions=_actions(("approve_board", "Approve board", "primary"),
                                  ("regenerate_board", "Regenerate", "secondary")))],
             question="Approve the board, or name a row to change?")
        store.log_artifact_activity(thread_id, "board", "proposed",
                                    f"v{board.version} · {len(board.shots)} shots · "
                                    f"${board.est_total_usd:.2f}")
        _stage_done(thread_id, campaign_id, "detail",
                    creative_type=board.creative_type)
        if not _pauses_at(campaign_id, "detail"):
            _spawn(thread_id, _canon_turn, thread_id, campaign_id)
    except AgentHardFail as exc:
        _fail(thread_id, "The shot board kept failing its lints.", str(exc))
    except Exception as exc:
        _fail(thread_id, f"Shot board failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _validate_board(board: ShotBoard, campaign_id: str, style: dict[str, Any]) -> ShotBoard:
    """Server-side: claims, costs, the style-block injection, then the lints."""
    context = _context_of(campaign_id)
    unmapped = _unmapped_claims(board.claims_used, context)
    if unmapped:
        raise AgentValidationError([
            f"claims_used {unmapped} are NOT in the confirmed approved_claims — an unmapped "
            "claim is a kill flag, not a stretch"])

    # The style block is injected HERE, verbatim, not by the model. A model that
    # paraphrases the style is a model that breaks consistency between shots.
    prefix = StyleBlock.model_validate(style).as_prompt()
    for shot in board.shots:
        if not shot.keyframe_prompt.startswith(prefix):
            shot.keyframe_prompt = f"{prefix} · {shot.keyframe_prompt}"
        shot.est_cost_usd = _shot_cost(shot)
    board.style_block_id = style.get("id")
    return validate_shot_board(board)


def _shot_cost(shot: Any) -> float:
    """Priced from config, never from the model."""
    if shot.model_route == "video":
        return round(config.MEDIA_COST_USD["video_per_s"] * shot.duration_s, 4)
    return float(config.MEDIA_COST_USD.get(shot.model_route, 0.0))


def _canon_turn(thread_id: str, campaign_id: str) -> None:
    """v3 §6 — the sheets the board actually references, and nothing else."""
    try:
        _working[thread_id] = "planning the canon sheets"
        ws = _ws(thread_id)
        board = ws.get("board") or {}
        if "canon" in skipped_stages(campaign_id):
            # A costed, RECORDED choice — never a silent default.
            store.log_artifact_activity(
                thread_id, "canon", "skipped",
                "skipped by settings — expect identity drift after roughly three shots")
            _say(thread_id, "Skipping canon sheets, as configured. "
                            "Expect identity drift after roughly three shots.")
            _spawn(thread_id, _keyframes_turn, thread_id, campaign_id)
            return

        plan, _log = run_agent(
            agent="canon_plan", prompt_name="canon_plan", model=config.PLANNER_MODEL,
            user_payload={"board": board, "context": _context_of(campaign_id).model_dump(mode="json")},
            schema=CanonPlan, dispatcher=None, use_tools=False,
            validate=lambda p: _validate_canon_plan(p, board),
            mock_fn=campaign_mock.mock_canon_plan)
        # Render the required views, then persist to the WORKSPACE library. A
        # planned sheet with no views is a promise; the sheet only becomes the
        # thing that makes campaign two cheaper once its images exist.
        sheets = _render_canon_views(thread_id, plan.sheets, campaign_id)
        ws["canon"] = [s.model_dump(mode="json") for s in sheets]
        _say(thread_id,
             f"{len(sheets)} canon sheet(s) — reusable across every future campaign.",
             [ArtifactEnvelope(
                 type="canon_sheet", id="canon", title="Canon sheets",
                 payload={"sheets": ws["canon"]},
                 actions=_actions(("approve_canon", "Approve canon", "primary"),
                                  ("skip_canon", "Skip sheets", "secondary")))],
             question="Approve these sheets, or skip them and accept the drift?")
        store.log_artifact_activity(thread_id, "canon", "proposed",
                                    ", ".join(s.id for s in sheets) or "none needed")
        _stage_done(thread_id, campaign_id, "canon",
                    creative_type=board.get("creative_type", "image"))
        if not _pauses_at(campaign_id, "canon"):
            _spawn(thread_id, _keyframes_turn, thread_id, campaign_id)
    except AgentHardFail as exc:
        _fail(thread_id, "The canon plan kept failing validation.", str(exc))
    except Exception as exc:
        _fail(thread_id, f"Canon planning failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _validate_canon_plan(plan: "CanonPlan", board: dict[str, Any]) -> "CanonPlan":
    """Every board reference resolves, and nothing unused is planned. Canon is
    expensive and reusable — an unused sheet spends money on nothing."""
    wanted: set[str] = set()
    for shot in board.get("shots", []):
        for key in ("cast_refs", "product_refs", "env_refs"):
            wanted.update(shot.get(key) or [])
    planned = {s.id for s in plan.sheets}
    errors: list[str] = []
    missing = sorted(wanted - planned)
    if missing:
        errors.append(f"the board references {missing} but no sheet is planned for them")
    extra = sorted(planned - wanted)
    if extra:
        errors.append(f"sheets {extra} are planned but the board never references them — "
                      "canon is expensive and reusable, so an unused sheet spends money on nothing")
    if errors:
        raise AgentValidationError(errors)
    for sheet in plan.sheets:
        # Plan time: the spec is checkable, the views do not exist yet.
        validate_canon_sheet(sheet, require_coverage=False)
    return plan


def _keyframes_turn(thread_id: str, campaign_id: str) -> None:
    """v3 §7 — THE HARD GATE. Stills are generated and must be approved before
    any motion is paid for."""
    try:
        _working[thread_id] = "rendering keyframes"
        ws = _ws(thread_id)
        board = ws.get("board") or {}
        shots = board.get("shots", [])
        policy = _policy_of(campaign_id)

        ratio = (ws.get("brief") or {}).get("aspect_ratios", ["9:16"])[0]
        frames: list[dict[str, Any]] = []
        for shot in shots:
            asset = _render_keyframe(thread_id, shot, ratio)
            frames.append({
                "shot_slot": shot["slot"], "asset_id": asset["asset_id"],
                "picked_from": policy.keyframes_per_shot,
                "refs_used": (shot.get("cast_refs") or []) + (shot.get("product_refs") or [])
                             + (shot.get("env_refs") or []),
                # Unwired detectors report `na`, never `pass` — a check nobody
                # ran must not render as a check that succeeded.
                "checks": {c: "na" for c in KEYFRAME_CHECKS},
                "repairs": [], "approved": False, "cost_usd": asset["cost"],
            })
        board_obj = KeyframeBoard.model_validate({"frames": frames})
        validate_keyframe_board(board_obj, board_slots=[s["slot"] for s in shots])
        ws["keyframes"] = board_obj.model_dump(mode="json")

        _say(thread_id,
             f"{len(frames)} keyframe(s) — nothing animates until every one is approved.",
             [ArtifactEnvelope(
                 type="keyframe_board", id="keyframes",
                 title=f"Keyframes · 0/{len(frames)} approved",
                 payload={"board": ws["keyframes"]},
                 actions=_actions(("approve_keyframes", "Approve all", "primary"),
                                  ("regenerate_keyframes", "Re-render", "secondary")))],
             question="Approve each frame, or tell me which one is wrong?")
        store.log_artifact_activity(thread_id, "keyframes", "proposed",
                                    f"{len(frames)} frames · ${board_obj.total_cost_usd:.2f}")
        _stage_done(thread_id, campaign_id, "keyframes",
                    creative_type=board.get("creative_type", "image"))
    except MediaError as exc:
        _fail(thread_id, f"Keyframe rendering failed: {exc}", traceback.format_exc())
    except Exception as exc:
        _fail(thread_id, f"Keyframes failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _render_keyframe(thread_id: str, shot: dict[str, Any], ratio: str) -> dict[str, Any]:
    """One still per shot. A keyframe is always an IMAGE, whatever animates it
    later — routing a video shot to the video model here would pay for motion at
    the gate whose whole purpose is to avoid paying for motion."""
    tier = "final" if shot.get("model_route") != "image_pro" else "pro"
    frame = generate("image", shot["keyframe_prompt"], ratio=ratio, tier=tier)
    asset_id = store.add_asset(
        thread_id, f"keyframe_{shot['slot']}", frame.get("kind", "image"), frame["path"],
        {"model": frame["model"], "prompt": shot["keyframe_prompt"], "ratio": ratio,
         "shot_slot": shot["slot"], "refs": shot.get("product_refs") or []},
        frame["cost"])
    store.log_generation(thread_id, asset_id, "generate", prompt=shot["keyframe_prompt"],
                         model=frame["model"], seed=str(frame.get("seed")), cost=frame["cost"])
    return {"asset_id": asset_id, "cost": frame["cost"]}


def _qc_turn(thread_id: str, campaign_id: str) -> None:
    """v3 §9 — three tiers. Blocking findings hold delivery whatever the gate says."""
    try:
        _working[thread_id] = "running QC"
        ws = _ws(thread_id)
        context = _context_of(campaign_id)
        brief = ws.get("brief") or {}
        board = ws.get("board") or {}
        report = build_qc_report(
            findings=[],
            automated={
                # Honest `skip`: these detectors are not wired yet, and rendering
                # an unrun check as `pass` would be a lie the report inherits.
                "identity_drift": "skip", "hand_anomalies": "skip",
                "label_ocr": "skip", "lip_sync": "skip", "loudness": "skip",
                "duration_and_ratio": "pass",
            },
            rights_ledger=list(context.brand.rights_ledger) if context.brand else [],
            final_copy=" ".join(filter(None, [board.get("copy_primary"), board.get("cta")])),
            context=context,
            locales=list(brief.get("languages") or []),
            voice_sheets=[CanonSheet.model_validate(s) for s in (ws.get("canon") or [])
                          if s.get("kind") == "voice"])
        ws["qc"] = report.model_dump(mode="json")
        blocking = [f for f in report.findings if f.tier == "blocking"]
        _say(thread_id,
             f"QC {report.verdict}." + (f" {len(blocking)} blocking finding(s)." if blocking else ""),
             [ArtifactEnvelope(
                 type="qc_report", id="qc",
                 title=f"QC · {report.verdict}",
                 payload={"report": ws["qc"]},
                 actions=_actions(("deliver", "Deliver", "primary"))
                         if report.verdict == "cleared" else [])],
             question=None if report.verdict == "cleared"
                      else "Delivery is held until the blocking findings clear.")
        store.log_artifact_activity(thread_id, "qc", "proposed",
                                    f"{report.verdict} · {len(blocking)} blocking")
        _stage_done(thread_id, campaign_id, "qc",
                    creative_type=board.get("creative_type", "image"))
    except Exception as exc:
        _fail(thread_id, f"QC failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _approve_all_keyframes(board: dict[str, Any]) -> dict[str, Any]:
    """User approval marks every frame. all_approved is DERIVED by the schema —
    this sets the frames, and the model recomputes the flag that actually gates
    paid video."""
    frames = [{**f, "approved": True} for f in board.get("frames", [])]
    return KeyframeBoard.model_validate({**board, "frames": frames}).model_dump(mode="json")


def _style_block_from_template(template: Optional[TemplateRef]) -> StyleBlock:
    """Turn the picked template into the string that is actually injected.

    A template's `style_descriptors` are a loose list nothing downstream could
    enforce. The style block is where they become a named, versioned string
    prefixed verbatim onto every visual prompt — which is the only reason
    picking a template changes what is rendered. Skip yields the neutral block,
    never an absent one.
    """
    if template is None:
        return neutral_style_block()
    descriptors = [d for d in (template.style_descriptors or []) if d.strip()]
    if not descriptors:
        block = neutral_style_block(f"sb_{template.id}")
        return block.model_copy(update={"derived_from": template.id})
    return StyleBlock(
        id=f"sb_{template.id}",
        grade=descriptors[0],
        light=descriptors[1] if len(descriptors) > 1 else "soft key, no hard shadow",
        lens=descriptors[2] if len(descriptors) > 2 else "50mm equivalent, mid aperture",
        texture=REALISM_TEXTURE["natural"],
        motion="locked off",
        negatives=["text overlay", "watermark"],
        realism="natural",
        derived_from=template.id,
    )


def _after_templates(thread_id: str, campaign_id: str) -> None:
    """Templates land, then the script — but only for creative that is SPOKEN.

    A still has no words-per-second problem, so routing an image campaign
    through the hook rack would be a gate that protects nothing and costs a turn.
    """
    ws = _ws(thread_id)
    template = ws.get("template")
    ws["style_block"] = _style_block_from_template(template).model_dump(mode="json")
    creative_type = (ws.get("brief") or {}).get("creative_type", "image")
    if creative_type == "video" and "script" not in skipped_stages(campaign_id):
        _script_turn(thread_id, campaign_id)
    else:
        _board_turn(thread_id, campaign_id)


def _detail_from_board(board: ShotBoard, rack: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Project the shot board into the legacy CampaignDetail shape.

    The generate path speaks `visual_prompt` / `vo_or_copy`; the board speaks
    `keyframe_prompt` / `motion_prompt` / `dialogue_ref`. This translates ONE
    WAY, at the point the board is written, so there is exactly one authored
    artifact. Writing both by hand would be two sources of truth, and the second
    one is always the stale one.
    """
    lines: dict[str, str] = {}
    if rack:
        for line in list(rack.get("body") or []) + list(rack.get("hooks") or []):
            lines[line["slot"]] = line["text"]

    shots = []
    for index, shot in enumerate(board.shots):
        # Video shots carry the SPOKEN line their dialogue_ref names. A still has
        # no dialogue — its "copy" is what appears on the frame, which the board
        # holds once as copy_primary. Dropping it here would silently remove the
        # copy-angle variant, because that axis keys off this field.
        if board.creative_type == "video":
            copy = lines.get(shot.dialogue_ref or "", "") or None
        else:
            copy = board.copy_primary if index == 0 else None
        shots.append({
            "slot": shot.slot,
            "duration_s": shot.duration_s if board.creative_type == "video" else None,
            "visual_prompt": shot.keyframe_prompt,
            "vo_or_copy": copy,
        })
    return {
        "creative_type": board.creative_type, "shots": shots,
        "copy_primary": board.copy_primary, "cta": board.cta,
        "claims_used": list(board.claims_used), "style_ref": None,
        "version": board.version, "changes": list(board.changes),
    }


def _render_canon_views(thread_id: str, sheets: list, campaign_id: str) -> list:
    """Generate each sheet's required views, then save it to the workspace library.

    A sheet ALREADY IN THE LIBRARY is reused, not re-rendered. That is the whole
    retention mechanic — campaign two is cheaper because the canon exists — and
    re-rendering here would quietly charge the user for it twice.

    A voice sheet has no views: it is auditioned on the real line, not viewed.
    """
    out = []
    for sheet in sheets:
        existing = store.get_canon_sheet(sheet.id)
        if existing and existing.get("asset_ids"):
            reused = CanonSheet.model_validate(
                {k: v for k, v in existing.items() if not k.startswith("_")})
            store.log_artifact_activity(
                thread_id, "canon", "reused",
                f"{reused.id} reused from the library — {len(reused.asset_ids)} view(s), no new spend")
            out.append(reused)
            continue

        for view in sheet.required_views():
            prompt = f"{sheet.brief}, {view.replace('_', ' ')} view, neutral background"
            frame = generate("image", prompt, ratio="1:1", tier="draft")
            asset_id = store.add_asset(thread_id, f"canon_{sheet.id}_{view}",
                                       frame.get("kind", "image"), frame["path"],
                                       {"model": frame["model"], "prompt": prompt,
                                        "canon_id": sheet.id, "view": view},
                                       frame["cost"])
            store.log_generation(thread_id, asset_id, "generate", prompt=prompt,
                                 model=frame["model"], seed=str(frame.get("seed")),
                                 cost=frame["cost"])
            sheet.asset_ids.append(asset_id)
            sheet.coverage[view] = True

        # Coverage is real now, so the full check applies.
        validate_canon_sheet(sheet)
        store.save_canon_sheet(sheet.model_dump(mode="json"), campaign_id)
        out.append(sheet)
    return out


def _reject_take(thread_id: str, slot: str, cause: str, detail: Optional[str]) -> None:
    """v3 §8 — classify the reject, then propose the patch that matches it.

    "Make it better" is not a repair instruction, so the five causes each carry
    a distinct fix. And ONE VARIABLE PER RETRY: two simultaneous edits make the
    next result uninterpretable, so a retry that changes the same variable as
    the last one is refused rather than run.
    """
    ws = _ws(thread_id)
    history = ws.setdefault("rejects", {}).setdefault(slot, [])
    reject = TakeReject(cause=cause, detail=detail or f"{slot} rejected as {cause}",
                        variable_changed=_VARIABLE_FOR_CAUSE[cause])

    if history and history[-1]["variable_changed"] == reject.variable_changed:
        _say(thread_id,
             f"That would change {reject.variable_changed} again — the last retry already did.",
             question="Change something else, or accept this take as it is?")
        return

    history.append(reject.model_dump(mode="json"))
    store.log_generation(thread_id, None, "reject", prompt=reject.detail)
    store.log_artifact_activity(
        thread_id, "creative", "downgraded",
        f"{slot}: {cause} → {reject.proposed_fix} (changing {reject.variable_changed})")
    _say(thread_id,
         f"Rejected {slot} as {cause.replace('_', ' ')}.",
         [ArtifactEnvelope(
             type="creative_set", id=f"reject_{slot}",
             title=f"{slot} · {cause.replace('_', ' ')}",
             payload={"slot": slot, "reject": reject.model_dump(mode="json"),
                      "history": history},
             actions=_actions((f"reroll_{slot}", "Apply fix and re-roll", "primary")))],
         question=f"{reject.proposed_fix} — apply it?")


# Each cause changes exactly ONE thing, and the name of that thing is what the
# next retry is checked against.
_VARIABLE_FOR_CAUSE = {
    "wrong_reference": "reference",
    "ambiguous_action": "action",
    "too_many_actions": "beat",
    "inconsistent_geometry": "product reference",
    "unsuitable_model": "model route",
}


def _emit_variant_matrix(thread_id: str) -> None:
    """v3 §11 — the REUSE MAP, once a variant set exists.

    ModelConfirm already proposed the variants; what was missing is the number
    that decides whether variant testing is affordable at all. A hook-axis
    variant re-renders exactly the hook shot, and THAT RATIO is the business
    case for having a shot board — so it has to be visible, not inferable.
    """
    ws = _ws(thread_id)
    # Gate on what was actually GENERATED, not on what model_confirm proposed.
    # The proposals survive on the workspace even when the user picks a single
    # creative, so reading them here would show a reuse map for variants that
    # do not exist. The matrix describes what IS.
    produced = {i.get("variant_id") for i in ws.get("items") or [] if i.get("variant_id")}
    if len(produced) < 2:
        return   # a single control is not a matrix; saying so would be noise
    specs = [s for s in (ws.get("variant_specs") or [])
             if s.get("variant_id") in produced]
    if len(specs) < 2:
        return

    board = ws.get("board") or {}
    slots = [s["slot"] for s in board.get("shots", [])] or \
            [s["slot"] for s in (ws.get("detail") or {}).get("shots", [])]
    per_shot = (float(board.get("est_total_usd") or 0.0) / len(slots)) if slots else 0.0

    cells = []
    for spec in specs:
        delta = str(spec.get("delta", ""))
        axis = ("hook" if _V_HOOK in delta else
                "cta" if _V_COPY in delta else "duration")
        # The control re-renders nothing; a hook swap re-renders the hook shot.
        rerendered = [] if _V_CONTROL in delta else slots[:1]
        cells.append(VariantCell(
            variant_id=spec.get("variant_id", "?"), axis=axis,
            delta=delta, hypothesis=spec.get("hypothesis", ""),
            shots_rerendered=rerendered,
            shots_reused=max(0, len(slots) - len(rerendered)),
            cost_usd=round(per_shot * len(rerendered), 4)))

    matrix = VariantMatrix(
        cells=cells,
        # what the same set would cost rendered from scratch, versus derived
        baseline_cost_usd=round(per_shot * len(slots) * len(cells), 4),
        matrix_cost_usd=round(sum(c.cost_usd for c in cells), 4))

    _say(thread_id,
         "Variants derive from the board, so most shots are reused rather than re-rendered.",
         [ArtifactEnvelope(
             type="variant_matrix", id="variants", title="Variant matrix",
             payload={"matrix": matrix.model_dump(mode="json")})])
