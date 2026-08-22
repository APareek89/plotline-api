"""Addendum-02 tests: creative thread sequence, §08 rules, Post Card object,
per-asset re-roll, seam QA, Plans lifecycle. MOCK_MEDIA is forced on — zero
network, zero spend; ffmpeg-dependent mocks fall back gracefully."""
from __future__ import annotations

import pytest

from app import config, creative, orchestrator, store
from app.schemas import UserEvent

FORM = {
    "name": "Creative e2e",
    "mode": "one_time",
    "content_area": "AI tools",
    "objective": "conversions",
    "platforms": ["instagram_reels", "youtube_shorts"],
    "cadence": {"type": "one_time", "concept_count": 4},
    "content_type": "text_video",
    "audience_sophistication": "practitioner",
    "tool_access": "screen recording",
    "positioning_depth": "power_user",
}


@pytest.fixture(autouse=True)
def mock_media(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MOCK_MEDIA", True)
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path / "assets")
    config.ASSET_DIR.mkdir(parents=True, exist_ok=True)
    creative._WORKSPACES.clear()


def _planned_series():
    context = orchestrator.run_intake(FORM, [])
    series_id = store.create_series(context.model_dump(mode="json"))
    run_id = store.create_run(series_id, "generate_plan")
    orchestrator.generate_plan(series_id, run_id)
    return series_id


def _drive_to_postcard(series_id: str) -> str:
    res = creative.start_creative_thread(series_id, "c01", "A")
    tid = res["thread"]["id"]
    # opening turn runs on a thread — call synchronously for the test
    import time
    for _ in range(50):
        if store.get_thread(tid)["stage"] == "route":
            break
        time.sleep(0.1)
    thread = store.get_thread(tid)
    creative._dispatch(thread, "route", "approve_route", "confidence", None)
    for _ in range(50):
        if store.get_thread(tid)["stage"] == "script":
            break
        time.sleep(0.1)
    creative._dispatch(store.get_thread(tid), "script", "approve_script", "script", None)
    for _ in range(50):
        if store.get_thread(tid)["stage"] == "voice":
            break
        time.sleep(0.1)
    creative._dispatch(store.get_thread(tid), "voice", "pick_voice_a", "voices", None)
    for _ in range(50):
        if store.get_thread(tid)["stage"] == "prompts":
            break
        time.sleep(0.1)
    creative._dispatch(store.get_thread(tid), "prompts", "generate_all", "prompts", None)
    for _ in range(100):
        if store.get_thread(tid)["stage"] == "endcard":
            break
        time.sleep(0.15)
    creative._dispatch(store.get_thread(tid), "endcard", "pick_end_2", "endcards", None)
    for _ in range(50):
        if store.get_thread(tid)["stage"] == "done":
            break
        time.sleep(0.1)
    return tid


def test_full_creative_sequence_to_post_card():
    series_id = _planned_series()
    tid = _drive_to_postcard(series_id)

    msgs = [m["envelope"] for m in store.get_messages(tid) if m["role"] == "agent"]
    seq = [a["type"] for m in msgs for a in m.get("artifacts", [])]
    # §02: confidence first, script before voice, prompts before assets, card last
    assert seq[0] == "concept" and seq[1] == "confidence_card"
    assert seq.index("script_package") < seq.index("voice_options") < seq.index("asset_prompt")
    assert seq[-1] == "post_card"

    cards = store.list_post_cards(series_id)
    assert len(cards) == 1
    card = cards[0]
    assert card["status"] == "ready" and card["concept_id"] == "c01"
    # captions per platform (§08 rule 10)
    assert set(card["post_content"]["caption_variants"]) == {"instagram_reels", "youtube_shorts"}
    assert card["post_content"]["hook_line"]
    assert len(card["media"]) == 5  # 4 shots + VO
    # mock mode = zero spend
    assert card["total_cost_credits"] == 0.0

    # Plans lifecycle: ready
    states = {s["concept_id"]: s for s in store.get_concept_states(series_id)}
    assert states["c01"]["production_status"] == "ready"


def test_cost_line_precedes_every_generation():
    """§08 rule 8: asset_prompt artifacts with cost exist BEFORE any asset."""
    series_id = _planned_series()
    tid = _drive_to_postcard(series_id)
    msgs = store.get_messages(tid)
    first_prompt_seq = next(m["seq"] for m in msgs
                            if m["role"] == "agent" and any(a["type"] == "asset_prompt" for a in m["envelope"].get("artifacts", [])))
    first_asset_seq = next(m["seq"] for m in msgs
                           if m["role"] == "agent" and any(a["type"] == "asset_set" for a in m["envelope"].get("artifacts", [])))
    assert first_prompt_seq < first_asset_seq
    prompts = [a for m in msgs if m["role"] == "agent"
               for a in m["envelope"].get("artifacts", []) if a["type"] == "asset_prompt"]
    assert all("cost" in p["payload"] for p in prompts)


def test_user_edited_prompt_used_verbatim():
    series_id = _planned_series()
    res = creative.start_creative_thread(series_id, "c01", "A")
    tid = res["thread"]["id"]
    import time
    for _ in range(50):
        if store.get_thread(tid)["stage"] == "route":
            break
        time.sleep(0.1)
    creative._dispatch(store.get_thread(tid), "route", "approve_route", "confidence", None)
    for _ in range(50):
        if store.get_thread(tid)["stage"] == "script":
            break
        time.sleep(0.1)
    creative._dispatch(store.get_thread(tid), "script", "approve_script", "script", None)
    for _ in range(50):
        if store.get_thread(tid)["stage"] == "voice":
            break
        time.sleep(0.1)
    creative._dispatch(store.get_thread(tid), "voice", "pick_voice_a", "voices", None)
    for _ in range(50):
        if store.get_thread(tid)["stage"] == "prompts":
            break
        time.sleep(0.1)
    ws = creative._pending(tid)
    ws["prompts"]["frame_01"] = "MY EXACT PROMPT, do not improve"
    creative._dispatch(store.get_thread(tid), "prompts", "generate_all", "prompts", None)
    for _ in range(100):
        if store.get_thread(tid)["stage"] == "endcard":
            break
        time.sleep(0.15)
    log = store.get_generation_log(tid)
    frame01 = next(g for g in log if g["event"] == "generate" and g["prompt"] and "MY EXACT PROMPT" in g["prompt"])
    assert frame01["prompt"] == "MY EXACT PROMPT, do not improve"


def test_per_asset_reroll_never_whole_deliverable():
    series_id = _planned_series()
    tid = _drive_to_postcard(series_id)
    before = {a["id"] for a in store.list_assets(tid)}
    creative._reroll_asset(tid, "shot_03", "slower push")
    after = store.list_assets(tid)
    new = [a for a in after if a["id"] not in before]
    assert len(new) == 1 and new[0]["slot"] == "shot_03"  # ONE new asset only
    log = store.get_generation_log(tid)
    assert any(g["event"] == "reroll" for g in log)


def test_seam_qa_one_free_auto_reroll_logged():
    series_id = _planned_series()
    tid = _drive_to_postcard(series_id)
    log = store.get_generation_log(tid)
    seam = [g for g in log if g["event"] == "seam_qa"]
    autoreroll = [g for g in log if g["event"] == "reroll" and "auto" in (g["prompt"] or "")]
    assert len(seam) == 1 and len(autoreroll) == 1


def test_mark_posted_updates_all_surfaces():
    series_id = _planned_series()
    tid = _drive_to_postcard(series_id)
    ws = creative._pending(tid)
    creative._dispatch(store.get_thread(tid), "done", "mark_posted", "postcard", None)
    card = store.get_post_card(ws["card_id"])
    assert card["status"] == "posted" and card["posted_at"]
    states = {s["concept_id"]: s for s in store.get_concept_states(series_id)}
    assert states["c01"]["production_status"] == "posted"


def test_text_only_concept_skips_creative_studio():
    form = {**FORM, "content_type": "text", "name": "Text only"}
    context = orchestrator.run_intake(form, [])
    series_id = store.create_series(context.model_dump(mode="json"))
    run_id = store.create_run(series_id, "generate_plan")
    orchestrator.generate_plan(series_id, run_id)
    res = creative.start_creative_thread(series_id, "c01", "A")
    assert res["thread"] is None
    assert res["post_card"]["media"] == []  # copy only
    assert res["post_card"]["post_content"]["caption_variants"]


def test_thread_ordinals_continue_series_sequence():
    series_id = _planned_series()
    planning = store.create_thread(series_id, kind="planning")
    res = creative.start_creative_thread(series_id, "c01", "A")
    assert res["thread"]["ordinal"] == planning["ordinal"] + 1
