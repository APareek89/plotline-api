"""Addendum-01 tests: envelope contract, command parser, thread driver flow,
anti-generic validators, coverage/PROVISIONAL, saturation guard, numbering."""
from __future__ import annotations

import pytest

from app import store, thread as thread_driver
from app.ccs import evidence_coverage, is_provisional
from app.commands import parse_command
from app.schemas import (
    AgentMessage,
    Concept,
    CreatorContext,
    ObjectiveFamily,
    UserEvent,
)
from app.validators import AgentValidationError, check_anti_generic

FORM = {
    "name": "AI Content",
    "mode": "series",
    "content_area": "AI tools",
    "description": "help creators edit faster with AI",
    "objective": "followers",
    "target_audience": "aspiring creators 20-30",
    "platforms": ["instagram_reels"],
    "cadence": {"type": "series", "posts_per_week": 3, "weeks": 2},
    "content_type": "text_video",
    "audience_sophistication": "practitioner",
    "tool_access": "screen recording, CapCut — no on-camera shoots",
    "positioning_depth": "power_user",
}


# ----------------------------------------------------------------- envelope --


def test_agent_message_envelope_rules():
    ok = AgentMessage(thread_id="t1", text="Two sentences max. Like this.", artifacts=[])
    assert ok.question is None
    with pytest.raises(Exception):
        AgentMessage(thread_id="t1", text="One. Two. Three sentences is prose.", artifacts=[])


def test_user_event_shapes():
    UserEvent(thread_id="t1", type="text", text="approve c1")
    UserEvent(thread_id="t1", type="action", action={"artifact_id": "c01", "event": "approve"})
    with pytest.raises(Exception):
        UserEvent(thread_id="t1", type="action")  # action event needs action
    with pytest.raises(Exception):
        UserEvent(thread_id="t1", type="text")  # text event needs text


# ------------------------------------------------------------ command parser --


def test_typed_approvals_normalize_to_action_events():
    assert parse_command("approve c3") == {"event": "approve_concept", "targets": ["c03"], "note": None}
    assert parse_command("approve the plan") == {"event": "approve_plan", "targets": [], "note": None}
    assert parse_command("pick f1 and f3") == {"event": "pick_format", "targets": ["f1", "f3"], "note": None}
    regen = parse_command("regenerate c2 — punchier hook, more money")
    assert regen["event"] == "regenerate_concept" and regen["targets"] == ["c02"]
    assert regen["note"] == "punchier hook, more money"


def test_ordinals_resolve_against_last_batch():
    parsed = parse_command("regenerate the second one, tighter", batch_order=["c04", "c05", "c06"])
    assert parsed["targets"] == ["c05"]


def test_panel_focus_resolves_this():
    parsed = parse_command("regenerate this — slower opening", panel_focus="c03")
    assert parsed["targets"] == ["c03"]


def test_non_command_falls_through():
    assert parse_command("what do you think about the hooks overall?") is None


# ------------------------------------------------- anti-generic validators --


def _concept(hook: str, direction: str) -> Concept:
    return Concept.model_validate({
        "id": "c01", "title": "t", "description": "d",
        "creative_direction": direction,
        "hook": {"verbal": hook, "first_frame": "f"},
        "format": "listicle_demo", "platform": "instagram_reels",
        "cta": "save this", "effort": "S", "asset_needs": [],
        "element_scores": [],
    })


def test_r3_banned_abstraction_rejected():
    errors: list = []
    check_anti_generic(_concept("This game-changer will boost productivity", "invoice on camera"), errors)
    assert any("banned abstraction" in e for e in errors)


def test_r3_specificity_floor():
    errors: list = []
    check_anti_generic(_concept("Some thoughts about improving things", "invoice on camera"), errors)
    assert any("specificity floor" in e for e in errors)


def test_r2_receipt_required():
    errors: list = []
    check_anti_generic(_concept("I cancelled a $200 tool yesterday", "energetic pacing, good vibes"), errors)
    assert any("R2 receipt" in e for e in errors)


def test_specific_receipted_concept_passes():
    errors: list = []
    check_anti_generic(_concept("I cancelled a $200 tool yesterday", "printed invoice torn on camera"), errors)
    assert errors == []


# --------------------------------------------------- coverage + provisional --


def test_evidence_coverage_and_provisional():
    def score(el, tags):
        return {"element": el, "addressed": True, "proposed_rating": "H",
                "how_addressed": "x", "why_not": None,
                "evidence": [{"tag": t, "source_id": "asset:A118" if t != "PRINCIPLE" else "model",
                              "claim": "c", "as_of": None} for t in tags]}
    concept = Concept.model_validate({
        "id": "c01", "title": "t", "description": "d",
        "creative_direction": "invoice on camera", "hook": {"verbal": "h", "first_frame": "f"},
        "format": "x", "platform": "instagram_reels", "cta": "c", "effort": "S", "asset_needs": [],
        "element_scores": [
            score("hook_strength", ["REF"]),          # 25 backed
            score("audience_alignment", ["STAT"]),    # 15 backed
            score("retention_structure", ["PRINCIPLE"]),  # not data-backed
            score("differentiation", ["PRINCIPLE"]),
            score("distribution_triggers", ["PRINCIPLE"]),
            score("platform_format_fit", ["PRINCIPLE"]),
            score("creator_fit_feasibility", ["PRINCIPLE"]),
        ],
    })
    cov = evidence_coverage(ObjectiveFamily.followers_reach, concept)
    assert cov == 40  # (25+15)/100
    assert not is_provisional(cov)
    assert is_provisional(39)


# ----------------------------------------------------- thread driver (e2e) --


def _start_thread():
    from app import orchestrator
    context = orchestrator.run_intake(FORM, [])
    series_id = store.create_series(context.model_dump(mode="json"))
    thread = store.create_thread(series_id, kind="planning")
    thread_driver._opening_turn(series_id, thread["id"])  # synchronous in tests
    return series_id, thread["id"]


def test_thread_numbering_never_reused():
    series_id = store.create_series({"name": "N"})
    t1 = store.create_thread(series_id)
    t2 = store.create_thread(series_id)
    assert (t1["ordinal"], t2["ordinal"]) == (1, 2)


def test_opening_turn_pins_context_then_formats_wait():
    series_id, thread_id = _start_thread()
    msgs = store.get_messages(thread_id)
    kinds = [[a["type"] for a in m["envelope"].get("artifacts", [])] for m in msgs if m["role"] == "agent"]
    assert kinds[0] == ["context_summary"]
    assert kinds[1] == ["inspiration_set"]
    assert kinds[2] == ["format_options"]
    fmt_msg = [m for m in msgs if m["role"] == "agent"][2]["envelope"]
    assert fmt_msg["question"] is not None  # PASS 0.5 waits for the pick
    assert store.get_thread(thread_id)["stage"] == "formats"
    # inspiration honesty flags survive the envelope
    insp = [m for m in msgs if m["role"] == "agent"][1]["envelope"]["artifacts"][0]
    assert insp["payload"]["sample_data"] is True and insp["payload"]["selection_enabled"] is False


def test_pick_format_plans_in_batches_with_verdicts_and_plan():
    series_id, thread_id = _start_thread()
    thread_driver._plan_turn(series_id, thread_id, ["f1", "f3"])
    msgs = [m["envelope"] for m in store.get_messages(thread_id) if m["role"] == "agent"]
    concept_batches = [m for m in msgs if any(a["type"] == "concept" for a in m.get("artifacts", []))]
    assert concept_batches, "no concept batches emitted"
    for batch in concept_batches:
        n = sum(1 for a in batch["artifacts"] if a["type"] == "concept")
        assert 1 <= n <= 4  # batches of 3-4 (last batch may be smaller)
        assert batch["text"]  # one-line frame
    first_concept = concept_batches[0]["artifacts"][0]
    payload = first_concept["payload"]
    assert payload["verdict"] is not None and payload["ccs"] is not None
    assert "coverage" in payload and "provisional" in payload
    assert [a["event"] for a in first_concept["actions"]] == ["approve", "feedback", "regenerate"]
    plan_msgs = [m for m in msgs if any(a["type"] == "plan" for a in m.get("artifacts", []))]
    assert plan_msgs and plan_msgs[-1]["question"] is not None
    # activity log captured proposal + any downgrades
    acts = store.get_artifact_activity(thread_id, "c01")
    assert any(a["event"] == "proposed" for a in acts)


def test_dual_path_approval_typed_and_action_same_result():
    series_id, thread_id = _start_thread()
    thread_driver._plan_turn(series_id, thread_id, ["f1"])
    # typed path
    thread_driver.handle_event(UserEvent(thread_id=thread_id, type="text", text="approve c1"))
    # action path
    thread_driver.handle_event(UserEvent(
        thread_id=thread_id, type="action", action={"artifact_id": "c02", "event": "approve"}
    ))
    states = {s["concept_id"]: s for s in store.get_concept_states(series_id)}
    assert states["c01"]["approved"] == 1 and states["c02"]["approved"] == 1
    # approve plan by text → series approved
    thread_driver.handle_event(UserEvent(thread_id=thread_id, type="text", text="approve the plan"))
    assert store.get_series(series_id)["status"] == "approved"
    assert store.get_thread(thread_id)["stage"] == "done"


def test_saturation_guard_below_threshold(monkeypatch):
    """niche_asset_count < 25 → the feedback lens must declare insufficient_data
    (mock does; the validator enforces it for real agents too)."""
    from app import orchestrator
    context = orchestrator.run_intake(FORM, [])
    plan, feedback, options, statuses = orchestrator.run_pipeline(
        context, lambda *a: None, chosen_formats=["f1"], niche_asset_count=7,
    )
    for verdict in feedback.concept_verdicts:
        assert verdict.lenses.saturation.insufficient_data is True
        assert "7" in (verdict.lenses.saturation.note or "")
