"""End-to-end pipeline test in mock mode: intake → plan → critique → refine →
options → persistence. Mock agents cite live fixture source_ids, so the whole
validation path (including citation resolution) is exercised for real."""
from app import orchestrator, store
from app.ccs import QUALIFY_THRESHOLD


FORM = {
    "name": "AI tools sprint",
    "mode": "series",
    "content_area": "AI tools",
    "description": "help creators edit faster with AI",
    "objective": "followers",
    "target_audience": "aspiring creators 20-30",
    "platforms": ["instagram_reels", "youtube_shorts"],
    "cadence": {"type": "series", "posts_per_week": 3, "weeks": 2},
    "content_type": "text_video",
    "audience_sophistication": "practitioner",
    "tool_access": "CapCut, Descript, screen recording — no on-camera shoots",
    "positioning_depth": "power_user",
}


def _create_series():
    context = orchestrator.run_intake(FORM, [])
    return store.create_series(context.model_dump(mode="json"))


def test_full_pipeline():
    series_id = _create_series()
    run_id = store.create_run(series_id, "generate_plan")
    orchestrator.generate_plan(series_id, run_id)

    series = store.get_series(series_id)
    assert series["status"] == "awaiting_review"

    bundle = store.get_plan(series_id)
    assert bundle is not None
    plan, feedback, options = bundle["plan"], bundle["feedback"], bundle["options"]

    # cadence → 6 slots
    assert len(plan["concepts"]) == 6

    # every verdict covers every applicable element (7 for followers)
    for verdict in feedback["concept_verdicts"]:
        assert len(verdict["element_verdicts"]) == 7
        assert verdict["lenses"]["saturation"]["source_id"] is not None

    states = store.get_concept_states(series_id)
    assert len(states) == 6
    by_status = {}
    for state in states:
        by_status.setdefault(state["status"], []).append(state)

    # the weak myth-buster concept was flagged, refined once, and re-judged up
    assert all(s["ccs"] > QUALIFY_THRESHOLD for s in states), states

    # options: exactly 3 per qualified concept, none for rework
    qualified_ids = {s["concept_id"] for s in states if s["status"] in ("qualified", "strong")}
    option_ids = {co["concept_id"] for co in options["concept_options"]}
    assert option_ids == qualified_ids
    for co in options["concept_options"]:
        assert len(co["options"]) == 3
        labels = {o["angle_label"] for o in co["options"]}
        assert len(labels) == 3

    # refine pass logged its changes
    assert plan["changes"], "refine pass should log changes[]"


def test_regenerate_concept_flow():
    series_id = _create_series()
    run_id = store.create_run(series_id, "generate_plan")
    orchestrator.generate_plan(series_id, run_id)

    states = store.get_concept_states(series_id)
    target = states[0]["concept_id"]

    regen_run = store.create_run(series_id, "regenerate_concept")
    orchestrator.regenerate_concept(series_id, target, "make the hook harder-hitting", regen_run)

    states_after = store.get_concept_states(series_id)
    target_state = next(s for s in states_after if s["concept_id"] == target)
    assert target_state["regen_count"] == 1

    bundle = store.get_plan(series_id)
    changed = [c for c in bundle["plan"]["changes"] if target in c]
    assert changed, "regenerate should log a change for the target concept"
    assert any("user note" in c for c in changed)


def test_approve_locks_series():
    series_id = _create_series()
    run_id = store.create_run(series_id, "generate_plan")
    orchestrator.generate_plan(series_id, run_id)

    for state in store.get_concept_states(series_id):
        store.set_concept_approved(series_id, state["concept_id"], True)
    states = store.get_concept_states(series_id)
    assert all(s["approved"] for s in states)


def test_run_events_stream_terminal():
    series_id = _create_series()
    run_id = store.create_run(series_id, "generate_plan")
    orchestrator.generate_plan(series_id, run_id)
    events, _ = orchestrator.run_events.since(run_id, 0)
    assert events, "pipeline should emit progress events"
    assert events[-1].get("terminal") is True
    stages = [e["stage"] for e in events]
    assert "plan" in stages and "critique" in stages and "done" in stages
