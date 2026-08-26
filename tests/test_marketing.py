"""Addendum-03 (Marketing Studio v2) acceptance tests — one test per check in
the v2 §Acceptance-checks list, plus the invariants those checks stand on.

MOCK_LLM + MOCK_MEDIA are forced on HERE ONLY (the product runs real models):
zero network, zero spend, deterministic. Long campaign work normally rides a
daemon thread — `_spawn` is made synchronous so the flow is assertable without
sleeping on it. Everything else is the real driver: real validators, real
council, real store.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import brand_extract, campaign, ccs as ccs_mod, config, main, store, threadkit
from app.agents import campaign_mock
from app.schemas import (
    AdCard,
    AgentMessage,
    CampaignBrief,
    CampaignContext,
    CampaignDetail,
    CampaignOption,
    CampaignOptions,
    Evidence,
    Feedback,
    ModelConfirm,
    Plan,
    SeatReview,
    SeatScore,
    UserEvent,
    objective_family,
)
from app import validators as validators_mod
from app.validators import AgentValidationError, validate_feedback

SETTINGS_TOOLTIP = "Model selection coming — using recommended models"

PRODUCT = {
    "name": "Ledger",
    "description": "invoicing app that saves 4 hours a month and files GST in 60 seconds",
    "image_upload_ids": ["up_1", "up_2", "up_3"],
}
CAMPAIGN = {
    "objective": "conversions",
    "target_audience": "indie founders 25-40 who file their own GST",
    "platforms": ["instagram_reels", "linkedin"],
    "description": "Q3 self-serve push",
    "creative_type": "image",
}
BRAND = {
    "url": "https://ledger.example",
    "palette": ["#0E1116", "#4353FF"],
    "font": "Inter",
    "tagline": "File it once",
    "approved_claims": ["files GST in 60 seconds", "saves 4 hours a month"],
    "banned_words": ["guaranteed"],
    "claims_confirmed": True,
}

TEMPLATE_MANIFEST = {
    "templates": [{
        "id": "t1", "label": "Hard flash", "thumb": "/samples/t1.png", "type": "image",
        "style_descriptors": ["brutalist grain", "hard direct flash"],
    }]
}


# ----------------------------------------------------------------- fixtures --


@pytest.fixture(autouse=True)
def marketing_env(monkeypatch, tmp_path, local_rag):
    monkeypatch.setattr(config, "MOCK_LLM", True)      # conftest sets it; be explicit
    monkeypatch.setattr(config, "MOCK_MEDIA", True)    # zero spend, zero network
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path / "assets")
    config.ASSET_DIR.mkdir(parents=True, exist_ok=True)
    # campaign.py binds `rag` at import — point it at the same in-process routing
    # client the autouse conftest fixture builds for everyone else.
    monkeypatch.setattr(campaign, "rag", local_rag)
    campaign._WORKSPACES.clear()
    threadkit._WORKSPACES.clear()

    def _sync(thread_id, fn, *args):
        campaign._ws(thread_id)["retry"] = (fn, args)  # Retry still has a target
        fn(*args)

    monkeypatch.setattr(campaign, "_spawn", _sync)


@pytest.fixture
def template_library(monkeypatch, tmp_path):
    """samples/templates/manifest.json with one style reference in it — the
    shipped manifest is empty, which makes step 4 auto-skip."""
    folder = tmp_path / "samples" / "templates"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(TEMPLATE_MANIFEST))
    monkeypatch.setattr(config, "ROOT", tmp_path)
    return TEMPLATE_MANIFEST["templates"][0]


# ------------------------------------------------------------------ helpers --


def _act(thread_id: str, artifact_id: str, event: str) -> None:
    campaign.handle_event(UserEvent(thread_id=thread_id, type="action",
                                    action={"artifact_id": artifact_id, "event": event}))


def _text(thread_id: str, text: str) -> None:
    campaign.handle_event(UserEvent(thread_id=thread_id, type="text", text=text))


def _envelopes(thread_id: str) -> list[dict]:
    return [m["envelope"] for m in store.get_messages(thread_id) if m["role"] == "agent"]


def _artifacts(thread_id: str, kind: str) -> list[dict]:
    return [a for env in _envelopes(thread_id) for a in env.get("artifacts", []) if a["type"] == kind]


def _first_seq(thread_id: str, kind: str) -> int:
    """Message sequence of the first agent turn carrying this artifact type."""
    for message in store.get_messages(thread_id):
        if message["role"] != "agent":
            continue
        if any(a["type"] == kind for a in message["envelope"].get("artifacts", [])):
            return message["seq"]
    raise AssertionError(f"no {kind} artifact on thread {thread_id}")


def _seed_block(campaign_id: str, block: str, data: dict) -> dict:
    """Merge one block into a campaign's stored context.

    This is what _seed_block() did for the card UI. The cards were
    removed entirely by owner decision on 2026-08-26, so the merge lives here
    now — in the tests that still need to stand a context up cheaply, rather
    than in the app where it would be product code nothing calls. The
    validation is kept, because that is the only part that was load-bearing.
    """
    series = store.get_series(campaign_id)
    raw = dict(series["context"])
    raw[block] = {**(raw.get(block) or {}), **(data or {})}
    context = CampaignContext.model_validate(raw)
    store.update_series_context(campaign_id, context.model_dump(mode="json"))
    return context.model_dump(mode="json")


def _filled(campaign_id: str, creative_type: str = "image") -> tuple[str, str]:
    """Named campaign + a fully populated context.

    The three-card path was removed entirely by owner decision on 2026-08-26,
    so there is no save_block to walk any more. Every test using this helper is
    about a stage AFTER intake, so it seeds the context directly — still
    validated through CampaignContext on the way in, which is the only part of
    save_block that was ever load-bearing. Intake itself is covered by the
    tests that go in through the composer the way a person does.
    """
    started = campaign.start_campaign(campaign_id)
    cid, tid = started["campaign_id"], started["thread"]["id"]
    context = CampaignContext.model_validate({
        "name": started["thread"] and store.get_series(cid)["name"],
        "product": PRODUCT,
        "campaign": {**CAMPAIGN, "creative_type": creative_type},
        "brand": BRAND,
    })
    store.update_series_context(cid, context.model_dump(mode="json"))
    return cid, tid


def _approve(thread_id: str, artifact_id: str, event: str) -> None:
    campaign.handle_event(UserEvent(
        thread_id=thread_id, type="action",
        action={"artifact_id": artifact_id, "event": event}))


def _pass_script(thread_id: str) -> None:
    """Walk the script gate if the flow is sitting at it.

    v3 puts the hook rack between the option and the board for SPOKEN creative.
    Image campaigns never stop here, so this is a no-op for them — the helper
    exists so a test can say "get me to the board" without caring which."""
    if store.get_thread(thread_id)["stage"] == "script":
        _approve(thread_id, "hook_rack", "approve_script")


def _ruminated(name: str, creative_type: str = "image") -> tuple[str, str]:
    """…through step 3: options on the thread, awaiting an approval.

    v3 inserted the BRIEF between the cards and the options, so this walks that
    gate. It is a real gate the user passes too — the helper approves it rather
    than bypassing it, so everything downstream is reached the way a person
    reaches it."""
    cid, tid = _filled(name, creative_type)
    campaign.begin_rumination(cid)
    _approve(tid, "brief", "approve_brief")
    return cid, tid


# ------------------------------------------- check 1: path b == path a schema --


def _scripted_intake(monkeypatch, blocks: dict) -> None:
    """campaign_mock ships no intake mock (prose parsing has no deterministic
    stand-in), so path b gets a scripted parser: it only ever ADDS the block the
    message names, exactly like the real intake agent is told to."""
    def fake_intake(payload, dispatcher):
        context = dict(payload["context"])
        message = payload["message"].lower()
        for block, data in blocks.items():
            if block in message and context.get(block) is None:
                context[block] = data
                break
        return context

    monkeypatch.setattr(campaign_mock, "mock_campaign_intake", fake_intake, raising=False)


def test_conversation_fills_the_identical_campaign_context(monkeypatch):
    """Conversation is elicitation UX, not a second data model.

    This used to compare path a against path b. The card path was removed
    entirely by owner decision on 2026-08-26, so there is no second path to
    compare against — but the property that mattered survives and is what is
    asserted now: talking to the agent produces exactly the context the rest of
    the pipeline reads, with no conversational-only shape sneaking in.
    """
    _scripted_intake(monkeypatch, {"product": PRODUCT, "campaign": CAMPAIGN,
                                   "brand": {**BRAND, "claims_confirmed": False}})

    structured_id, _ = _filled("Seeded")

    started = campaign.start_campaign("Talked")
    conversational_id, tid = started["campaign_id"], started["thread"]["id"]
    assert store.get_thread(tid)["stage"] == "intake", "naming must land straight in the conversation"
    for turn in ("the product is Ledger", "campaign objective is conversions", "brand details next"):
        _text(tid, turn)
    _seed_block(conversational_id, "brand", {"claims_confirmed": True})  # the one-tap confirm

    path_a = store.get_series(structured_id)["context"]
    path_b = store.get_series(conversational_id)["context"]
    assert CampaignContext.model_validate(path_a) and CampaignContext.model_validate(path_b)
    assert {k: v for k, v in path_a.items() if k != "name"} == \
           {k: v for k, v in path_b.items() if k != "name"}
    assert campaign.context_filled(path_b) == {"product": True, "campaign": True, "brand": True}
    assert campaign.missing_blocks(path_b) == []

    # ≤1 question per turn, and progress chips track the same three blocks
    progress = _artifacts(tid, "intake_progress")
    # No "brand.claims_confirmed" step: confirming claims GRANTS permission to
    # make them, it is not a toll on getting started. An unconfirmed brand just
    # has an empty approved list, so any claim used is unmapped and kill-flagged.
    # One entry shorter than it used to be: the turn that only picked a path is
    # gone, so the first thing the user says already fills a block.
    assert progress[0]["payload"]["next_field"] == "opening"
    assert [p["payload"]["next_field"] for p in progress[1:]] == ["campaign", "brand", None]
    for envelope in _envelopes(tid):
        AgentMessage.model_validate(envelope)


def test_path_b_intake_may_not_rename_drop_or_self_confirm():
    """The intake agent is a parser with eyes: the campaign name, the already
    filled blocks and the claims confirmation are the user's, not its own."""
    current = CampaignContext.model_validate(
        {"name": "Keep me", "product": PRODUCT, "brand": {**BRAND, "claims_confirmed": False}})
    renamed = current.model_copy(update={"name": "Renamed by the agent"})
    dropped = current.model_copy(update={"product": None})
    self_confirmed = current.model_copy(
        update={"brand": current.brand.model_copy(update={"claims_confirmed": True})})

    for bad, needle in ((renamed, "name must stay"),
                        (dropped, "dropped the already-filled product"),
                        (self_confirmed, "claims_confirmed is the user's one-tap confirmation")):
        with pytest.raises(AgentValidationError) as exc:
            campaign._validate_intake(bad, current)
        assert needle in str(exc.value)
    assert campaign._validate_intake(current, current) is current


# ------------------------------------- check 2: brand fetch is user-confirmable --


def test_brand_fetch_populates_but_nothing_is_authoritative_until_the_user_saves(monkeypatch):
    started = campaign.start_campaign("Brand fetch")
    cid = started["campaign_id"]
    _seed_block(cid, "product", PRODUCT)

    extracted = {"palette": ["#0E1116", "#4353FF", "#E5312B"], "font": "Inter",
                 "logo_url": "https://ledger.example/logo.png", "tagline": "File it once",
                 "source_url": "https://ledger.example", "notes": []}
    calls: list[str] = []

    def fake_extract(url, timeout=12.0):
        calls.append(url)          # system pipeline, never an agent tool
        return extracted

    monkeypatch.setattr(brand_extract, "extract", fake_extract)

    fetched = main.brand_fetch(cid, main.BrandFetchBody(url="ledger.example"))
    assert calls == ["ledger.example"]
    assert fetched["palette"] and fetched["font"] and fetched["logo_url"] and fetched["tagline"]

    # nothing landed: the fetch is a candidate set, the Brand card is the truth
    assert store.get_series(cid)["context"]["brand"] is None
    assert campaign.context_filled(store.get_series(cid)["context"])["brand"] is False

    candidates = main.claims_extract(cid)
    assert "saves 4 hours a month" in " ".join(candidates["approved_claims"])
    assert store.get_series(cid)["context"]["brand"] is None  # candidates aren't saved either

    # Unconfirmed claims no longer block the rumination — but they are also not
    # APPROVED, so they buy the campaign nothing. The list stays unusable until
    # the user confirms it; that is where the compliance line sits now.
    _seed_block(cid, "campaign", CAMPAIGN)
    unconfirmed = _seed_block(cid, "brand", {"url": "https://ledger.example",
                                                     "approved_claims": candidates["approved_claims"]})
    assert campaign.missing_blocks(unconfirmed) == []
    assert unconfirmed["brand"]["claims_confirmed"] is False
    context = CampaignContext.model_validate(unconfirmed)
    detail = CampaignDetail(
        creative_type="image",
        shots=[{"slot": "slide_01", "duration_s": None,
                "visual_prompt": "a split screen of the product", "vo_or_copy": "see it"}],
        copy_primary="c", cta="Shop",
        claims_used=[candidates["approved_claims"][0]],   # an UNCONFIRMED claim
        style_ref=None, version=1, changes=[])
    with pytest.raises(AgentValidationError) as verr:
        campaign._validate_detail(detail, context, None)
    assert "NOT in the confirmed approved_claims" in str(verr.value)

    # the user edits what the extractor proposed, then confirms — their edit wins
    saved = _seed_block(cid, "brand", {
        "palette": extracted["palette"][:2],           # dropped the third colour
        "font": "Inter Tight",                         # corrected the font
        "tagline": extracted["tagline"],
        "claims_confirmed": True,
    })
    assert saved["brand"]["palette"] == ["#0E1116", "#4353FF"]
    assert saved["brand"]["font"] == "Inter Tight"
    assert campaign.missing_blocks(saved) == []
    assert main.start_campaign(cid) == {"ok": True}


# ------------------------------------------- check 3: unmapped claim → kill flag --


def test_unmapped_claim_produces_a_kill_flag_and_withholds_the_option(monkeypatch):
    """The reviewer holds the kill flag: a persuasion claim outside the CONFIRMED
    approved list is killed, and a killed option never reaches the user.

    This used to belong to the Brand seat. With one Marketing Expert (owner
    decision 2026-08-25) the compliance pass is the reviewer's own mechanical
    first pass — the rule is unchanged, the holder is."""
    real = campaign._chair_merge

    def reviewer_kills(payload, dispatcher):
        out = real(payload, dispatcher)
        for verdict in out["concept_verdicts"]:
            verdict["kill_flags"] = ["unsubstantiated_claim"]
            verdict["lenses"]["claims_safety"] = (
                "unsubstantiated_claim: '3x faster than QuickBooks' is not confirmed")
        return out

    monkeypatch.setattr(campaign, "_chair_merge", reviewer_kills)
    cid, tid = _ruminated("Killed")

    assert _artifacts(tid, "campaign_option") == []     # nothing shipped
    assert _artifacts(tid, "escalation")[-1]["title"] == "All options kill-flagged"
    downgrades = [a for a in store.get_artifact_activity(tid, "o1") if a["event"] == "downgraded"]
    assert downgrades and "unsubstantiated_claim" in downgrades[0]["detail"]
    assert store.get_campaign_status(cid) == "draft"    # never promoted to planned


def test_unmapped_claim_never_reaches_the_campaign_detail(monkeypatch):
    """The same rule as code, one layer down: claims_used ⊆ confirmed claims is
    a hard validation on the detail, not a prompt instruction."""
    cid, tid = _ruminated("Claim gate")
    context = campaign._context_of(cid)
    detail_of = campaign_mock.mock_campaign_detail

    with pytest.raises(AgentValidationError) as exc:
        campaign._validate_detail(
            CampaignDetail.model_validate({
                **detail_of({"context": context.model_dump(mode="json")}, None),
                "claims_used": ["3x faster than QuickBooks"],
            }),
            context, None)
    assert "3x faster than QuickBooks" in str(exc.value) and "kill flag" in str(exc.value)

    # …and through the live path. v3 upgraded the detail into the SHOT BOARD, so
    # the claim now has to be refused there — same rule, same helper, new artifact.
    board_of = campaign_mock.mock_shot_board

    def unmapped_board(payload, dispatcher):
        return {**board_of(payload, dispatcher), "claims_used": ["3x faster than QuickBooks"]}

    monkeypatch.setattr(campaign_mock, "mock_shot_board", unmapped_board)
    _act(tid, "o1", "approve")
    _pass_script(tid)
    assert _artifacts(tid, "campaign_detail") == []     # no board, no generation path
    assert _artifacts(tid, "escalation")[-1]["title"].startswith("The shot board kept failing")
    assert store.list_assets(tid) == []


# ------------------------------------ check 4: Skip = no style constraint at all --


def test_skip_on_templates_leaves_no_style_constraint(template_library):
    picked_style = template_library["style_descriptors"]

    cid, tid = _ruminated("Skipped")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    picker = _artifacts(tid, "template_picker")[-1]
    assert picker["payload"]["skip_allowed"] is True
    assert {a["event"] for a in picker["actions"]} == {"pick_t1", "skip"}

    _act(tid, "templates", "skip")
    detail = _artifacts(tid, "campaign_detail")[-1]["payload"]["detail"]
    assert detail["style_ref"] is None
    _act(tid, "detail", "generate_creative")
    _act(tid, "confirm", "generate_single")

    prompts = [s["visual_prompt"] for s in detail["shots"]]
    prompts += [store.get_asset(i["asset_id"])["params"]["prompt"] for i in campaign._ws(tid)["items"]]
    for descriptor in picked_style:
        assert not any(descriptor in p for p in prompts), f"{descriptor!r} leaked after Skip"
    assert all("style reference" not in p.lower() for p in prompts)
    assert all(store.get_asset(i["asset_id"])["params"]["style_ref"] is None
               for i in campaign._ws(tid)["items"])

    # …and the same flow WITH a template proves the descriptors do travel when
    # picked. v3 changed the MECHANISM, not the invariant: TemplateRef's loose
    # descriptor list is now compiled into a style_block whose string is
    # prefixed verbatim onto every visual prompt. That is the only reason
    # picking a template changes what gets rendered.
    cid2, tid2 = _ruminated("Styled")
    _act(tid2, "o1", "approve")
    _pass_script(tid2)
    _act(tid2, "templates", "pick_t1")
    card = _artifacts(tid2, "campaign_detail")[-1]["payload"]
    styled = card["detail"]
    assert card["style_block"]["derived_from"] == "t1"
    _act(tid2, "detail", "generate_creative")
    _act(tid2, "confirm", "generate_single")
    styled_prompts = [store.get_asset(i["asset_id"])["params"]["prompt"]
                      for i in campaign._ws(tid2)["items"]]
    assert all(all(d in p for d in picked_style) for p in styled_prompts)


def test_skip_is_enforced_on_the_detail_not_just_requested():
    """A planner that returns a style_ref after Skip is rejected outright."""
    cid, _ = _filled("Skip gate")
    context = campaign._context_of(cid)
    detail = CampaignDetail.model_validate(
        campaign_mock.mock_campaign_detail(
            {"context": context.model_dump(mode="json"),
             "template": {"id": "t1", "type": "image", "style_descriptors": ["brutalist grain"]}},
            None))
    assert detail.style_ref is not None
    with pytest.raises(AgentValidationError) as exc:
        campaign._validate_detail(detail, context, None)   # template=None → Skip
    assert "style_ref must be null" in str(exc.value)


# ------------------------------- check 5: the Settings icon is honestly disabled --


def _request_body_properties(spec: dict) -> set[tuple[str, str]]:
    """(path, property) for every property reachable from any request body."""
    schemas = spec.get("components", {}).get("schemas", {})
    found: set[tuple[str, str]] = set()
    for path, operations in spec["paths"].items():
        queue: list = []
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            for media in ((operation.get("requestBody") or {}).get("content") or {}).values():
                queue.append(media.get("schema") or {})
        seen: set[str] = set()
        while queue:
            node = queue.pop()
            if not isinstance(node, dict):
                continue
            ref = node.get("$ref")
            if ref:
                key = ref.rsplit("/", 1)[-1]
                if key not in seen:
                    seen.add(key)
                    queue.append(schemas.get(key, {}))
                continue
            for prop, sub in (node.get("properties") or {}).items():
                found.add((path, prop))
                queue.append(sub)
            for key in ("items", "additionalProperties"):
                if isinstance(node.get(key), dict):
                    queue.append(node[key])
            for key in ("anyOf", "oneOf", "allOf"):
                queue.extend(node.get(key) or [])
    return found


def test_settings_icon_is_disabled_and_hides_no_live_capability():
    """The icon is rendered-but-disabled with a tooltip; that is honest only if
    nothing behind it takes a model. The whole API is swept: no path, no query
    or path parameter, and no request-body field names a model.

    This check got STRICTER when the pre-Addendum-03 app was deleted: the DIY
    route's `model_tier` was the one documented exception, and it is gone, so
    the exception set is now empty. Never re-add one."""
    assert ModelConfirm.model_fields["settings_note"].default == SETTINGS_TOOLTIP

    spec = main.app.openapi()
    assert not [p for p in spec["paths"] if "model" in p.lower()]
    for path, operations in spec["paths"].items():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters") or []:
                assert "model" not in parameter["name"].lower(), f"{path} takes {parameter['name']}"

    model_ish = {(path, prop) for path, prop in _request_body_properties(spec)
                 if "model" in prop.lower()}
    assert model_ish == set(), f"a request body names a model: {model_ish}"
    # the fixed stack still exists server-side — it is chosen FOR the user
    assert {f"image_{tier}" for tier in ("draft", "final", "pro")} <= set(config.MEDIA_MODELS)

    # The campaign surfaces are extra="forbid" — a smuggled override is refused.
    # This used to go through the block PUT route. That route died with the card
    # path, so the check moved DOWN to the schema it was really about, which is
    # stronger: it now holds for every caller, not just the one route.
    for block, override in (("campaign", {**CAMPAIGN, "model": "fal-ai/some-other-model"}),
                            ("product", {**PRODUCT, "image_model": "x"}),
                            ("brand", {**BRAND, "video_model": "x"})):
        with pytest.raises(ValidationError):
            CampaignContext.model_validate({"name": "No overrides", block: override})
    with pytest.raises(ValidationError):
        UserEvent(thread_id="t1", type="action",
                  action={"artifact_id": "confirm", "event": "generate_single", "model": "x"})


def test_model_confirm_names_the_fixed_stack_whatever_the_user_types():
    cid, tid = _ruminated("Fixed stack", creative_type="video")
    _act(tid, "o1", "approve")
    _pass_script(tid)

    _text(tid, "use fal-ai/flux-pro instead of veo")   # there is no such lever
    assert _envelopes(tid)[-1]["text"] == "Didn't catch a campaign command."

    _act(tid, "detail", "generate_creative")
    confirm = _artifacts(tid, "model_confirm")[-1]["payload"]["confirm"]
    assert confirm["recommended_model"] == config.MEDIA_MODELS["video"]
    assert confirm["reason"] and confirm["settings_note"] == SETTINGS_TOOLTIP

    _act(tid, "confirm", "generate_single")
    assert store.list_assets(tid)
    assert all("flux" not in json.dumps(a["params"]) for a in store.list_assets(tid))


# --------------------- check 6: every asset in the Creative tab, params + cost --


def test_every_generated_asset_lands_in_the_creative_set_with_params_and_cost():
    cid, tid = _ruminated("Creative tab", creative_type="video")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")
    _act(tid, "confirm", "generate_single")

    items = _artifacts(tid, "creative_set")[-1]["payload"]["items"]
    assert items and items == campaign._ws(tid)["items"]

    surfaced = {i["asset_id"] for i in items} | {i["cover_asset_id"] for i in items if i["cover_asset_id"]}
    assert surfaced == {a["id"] for a in store.list_assets(tid)}, "a generated asset never surfaced"

    log = store.get_generation_log(tid)
    for item in items:
        assert item["preview_url"].endswith(f"/{item['asset_id']}/file")
        assert item["status"] == "ready" and item["ratio"] and item["cost"] is not None
        for asset_id in (item["asset_id"], item["cover_asset_id"]):
            if not asset_id:
                continue
            asset = store.get_asset(asset_id)
            params = asset["params"]
            assert params["model"] and params["prompt"] and params["ratio"] == item["ratio"]
            assert set(params) >= {"model", "prompt", "ratio", "seed", "variant_id",
                                   "source_slot", "product_pack", "style_ref"}
            assert float(asset["cost"]) == pytest.approx(
                float(next(g["cost"] for g in log if g["asset_id"] == asset_id)))
    assert campaign._ws(tid)["spent"] == pytest.approx(sum(a["cost"] for a in store.list_assets(tid)))


def test_a_rerolled_asset_surfaces_too_and_the_audit_trail_survives():
    cid, tid = _ruminated("Re-roll", creative_type="image")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")
    _act(tid, "confirm", "generate_single")
    slot = campaign._ws(tid)["items"][0]["slot"]
    before = {a["id"] for a in store.list_assets(tid)}

    _act(tid, "creative", f"reroll_{slot}")
    fresh = [a for a in store.list_assets(tid) if a["id"] not in before]
    assert len(fresh) == 1 and fresh[0]["slot"] == slot        # per-asset only
    latest = _artifacts(tid, "creative_set")[-1]["payload"]["items"]
    assert [i["asset_id"] for i in latest] == [fresh[0]["id"]]
    assert store.get_asset(fresh[0]["id"])["params"]["reroll"] is True
    # the superseded asset leaves the Creative tab but never the generation log
    log = store.get_generation_log(tid)
    assert {g["asset_id"] for g in log} >= before | {fresh[0]["id"]}
    assert any(g["event"] == "reroll" for g in log)


# ------------------- check 7: the single-vs-variants question precedes generation --


def test_no_generation_without_a_model_confirm_and_an_explicit_event():
    # video → 2 shots, so every count (single, 2, 3) is genuinely offerable;
    # a 1-shot image detail correctly offers fewer (see the sibling test).
    cid, tid = _ruminated("Gate", creative_type="video")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    assert store.get_thread(tid)["stage"] == "detail"

    _act(tid, "confirm", "generate_single")            # jumping the gate
    assert store.list_assets(tid) == []
    assert _artifacts(tid, "model_confirm") == []
    assert _artifacts(tid, "creative_set") == []
    assert _envelopes(tid)[-1]["text"].startswith("Nothing changed")

    _act(tid, "detail", "generate_creative")
    confirm_seq = _first_seq(tid, "model_confirm")
    payload = _artifacts(tid, "model_confirm")[-1]
    assert payload["payload"]["cost_single"] > 0        # cost BEFORE any spend
    assert {a["event"] for a in payload["actions"]} >= {
        "generate_single", "generate_variants_2", "generate_variants_3"}
    q = _envelopes(tid)[-1]["question"]
    assert q and "variants" in q["text"].lower()
    assert store.list_assets(tid) == []                 # the card alone renders nothing

    _act(tid, "confirm", "generate_single")
    assert _first_seq(tid, "creative_set") > confirm_seq
    assert store.list_assets(tid)


def test_a_variant_set_is_never_generated_without_an_explicit_count():
    cid, tid = _ruminated("Count")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")

    _act(tid, "confirm", "generate_variants_0")         # button with no count
    assert store.list_assets(tid) == []
    assert _envelopes(tid)[-1]["question"]["text"] == "Two variants or three?"

    _text(tid, "give me variants")                      # typed, still no count
    assert store.list_assets(tid) == []
    assert _envelopes(tid)[-1]["text"] == "Variants need a count."

    _text(tid, "2 variants")                            # explicit count
    assert {i["variant_id"] for i in campaign._ws(tid)["items"]} == {"A", "B"}


def test_variants_carry_distinct_deltas_and_share_one_variant_group_id():
    cid, tid = _ruminated("Variants", creative_type="video")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")

    specs = _artifacts(tid, "model_confirm")[-1]["payload"]["confirm"]["variants_proposed"]
    assert [s["variant_id"] for s in specs] == ["A", "B", "C"]
    assert len({s["delta"] for s in specs}) == len(specs)          # named, distinct
    assert len({s["hypothesis"] for s in specs}) == len(specs)
    assert all(s["cost_usd"] > 0 for s in specs)

    _act(tid, "confirm", "generate_variants_3")
    ws = campaign._ws(tid)
    rendered: dict[str, list[str]] = {}
    for item in ws["items"]:
        rendered.setdefault(item["variant_id"], []).append(
            store.get_asset(item["asset_id"])["params"]["prompt"])
    assert set(rendered) == {"A", "B", "C"}
    # deltas are MECHANICAL: a named delta that changed nothing is a rewording
    assert rendered["B"] != rendered["A"] and rendered["C"] != rendered["A"]

    _act(tid, "creative", "accept_all")
    _act(tid, "qc", "deliver")          # v3: QC gates delivery
    cards = store.list_ad_cards(cid)
    assert {c["variant_id"] for c in cards} == {"A", "B", "C"}
    assert len({c["variant_group_id"] for c in cards}) == 1
    assert all(c["variant_group_id"] for c in cards)
    assert len({c["naming"] for c in cards}) == 3


def test_single_shot_variants_are_not_identical_renders():
    cid, tid = _ruminated("Single shot", creative_type="image")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")
    assert len(campaign._ws(tid)["detail"]["shots"]) == 1
    _act(tid, "confirm", "generate_variants_2")

    by_variant: dict[str, list[str]] = {}
    for item in campaign._ws(tid)["items"]:
        by_variant.setdefault(item["variant_id"], []).append(
            store.get_asset(item["asset_id"])["params"]["prompt"])
    assert by_variant["B"] != by_variant["A"]


# ------------------------------------------- check 8: Ad Card spec-table guard --


def test_ad_card_fails_on_a_missing_or_off_spec_ratio():
    cid, tid = _ruminated("Ad Card")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")
    _act(tid, "confirm", "generate_single")
    _act(tid, "creative", "accept_all")
    _act(tid, "qc", "deliver")          # v3: QC gates delivery

    card = store.list_ad_cards(cid)[0]
    assert AdCard.model_validate(card)
    # every ratio on the card is a ratio we actually rendered — no phantom crops
    assert set(card["ratios"]) == {m["ratio"] for m in card["media"]}
    assert set(card["placements"]) == set(CAMPAIGN["platforms"])

    for broken, needle in (
        ({**card, "ratios": []}, "at least 1 item"),
        ({k: v for k, v in card.items() if k != "ratios"}, "Field required"),
        ({**card, "ratios": ["3:2"]}, "outside the placement spec table"),
        ({**card, "ratios": ["9:16", "2:1"]}, "outside the placement spec table"),
        ({**card, "creative_type": "carousel"}, "creative_type"),
    ):
        with pytest.raises(ValidationError) as exc:
            AdCard.model_validate(broken)
        assert needle in str(exc.value)


def test_ad_card_credits_account_for_every_paid_render(monkeypatch):
    """MOCK_MEDIA renders at $0, which hides cost bugs — price the mock renders
    with the same estimator the confirm card quotes from, then compare."""
    from app.media import estimate_cost, generate as real_generate

    def priced(kind, prompt, **kwargs):
        out = real_generate(kind, prompt, **kwargs)
        return {**out, "cost": estimate_cost(kind, duration_s=kwargs.get("duration_s", 4.0),
                                             tier=kwargs.get("tier", "final"))}

    monkeypatch.setattr(campaign, "generate", priced)

    cid, tid = _ruminated("Card cost", creative_type="video")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")
    quoted = _artifacts(tid, "model_confirm")[-1]["payload"]["cost_single"]
    _act(tid, "confirm", "generate_single")
    _act(tid, "creative", "accept_all")
    _act(tid, "qc", "deliver")          # v3: QC gates delivery

    card = store.list_ad_cards(cid)[0]
    assert store.campaign_spend(cid) == pytest.approx(quoted)      # we spent the quote
    assert card["total_cost_credits"] == pytest.approx(
        round(store.campaign_spend(cid) / threadkit.CREDIT_USD, 1))


# ------------------------------------------------ check 9: campaign lifecycle --


def test_campaign_lifecycle_lands_in_the_store():
    cid, tid = _filled("Lifecycle")
    assert store.get_campaign_status(cid) == "draft"

    # v3: the brief sits between the cards and the options, so the campaign is
    # not "planned" until the options actually land behind it.
    campaign.begin_rumination(cid)
    assert store.get_campaign_status(cid) == "draft"
    _approve(tid, "brief", "approve_brief")
    assert store.get_campaign_status(cid) == "planned"

    _act(tid, "o1", "approve")

    _pass_script(tid)
    _act(tid, "detail", "generate_creative")
    assert store.get_campaign_status(cid) == "planned"      # confirming spends nothing

    _act(tid, "confirm", "generate_single")
    assert store.get_campaign_status(cid) == "in_production"

    _act(tid, "creative", "accept_all")
    _act(tid, "qc", "deliver")          # v3: QC gates delivery
    assert store.get_campaign_status(cid) == "ready"

    card = store.list_ad_cards(cid)[0]
    _act(tid, card["id"], "mark_live")
    assert store.get_campaign_status(cid) == "live"
    assert store.get_ad_card(card["id"])["status"] == "live"
    assert store.get_thread(tid)["stage"] == "done"

    row = next(r for r in main.list_campaigns() if r["id"] == cid)
    assert row["status"] == "live" and row["creative_count"] == 1
    assert row["objective"] == "conversions" and row["thread_id"] == tid
    assert row["spend_credits"] == pytest.approx(
        round(store.campaign_spend(cid) / threadkit.CREDIT_USD, 1))

    with pytest.raises(ValueError):
        store.set_campaign_status(cid, "archived")          # only the five states
    for envelope in _envelopes(tid):
        AgentMessage.model_validate(envelope)               # ≤2 sentences, ≤1 question


# ------------------------------- check 10: one reviewer, the v1 Feedback shape --


def test_the_reviewer_judges_blind_and_returns_the_v1_feedback(monkeypatch):
    """Owner decision 2026-08-25: after the planner, ONE Marketing Expert reviews
    the draft and feeds refinement. Three blind seats plus a chair were four LLM
    calls whose combined product was one Feedback; the reviewer emits it
    directly, so the consolidation step is gone rather than faster.

    What did NOT change: the reviewer never sees the planner's self-ratings, and
    the Feedback contract the refine loop consumes is untouched."""
    seen: list[dict] = []
    real = campaign._chair_merge

    def spy(payload, dispatcher):
        seen.append({"payload": payload, "dispatcher": dispatcher})
        return real(payload, dispatcher)

    monkeypatch.setattr(campaign, "_chair_merge", spy)
    cid, tid = _ruminated("Reviewer")

    assert seen, "the reviewer never ran"
    for record in seen:
        payload = record["payload"]
        # judges from doctrine: no tools, no retrieval slice to leak
        assert record["dispatcher"] is None
        # no stakeholder seats configured, so there is nothing to consolidate
        assert "stakeholder_seats" not in payload
        # and no planner self-ratings reach the reviewer — its judgment is its own
        assert all(c["element_scores"] == [] for c in payload["plan"]["concepts"])
        assert payload["options"] and payload["stage"] == "campaign_options"

    context = campaign._context_of(cid)
    shadow = campaign._shadow_context(context)
    options = CampaignOptions(options=list(campaign._ws(tid)["options"].values()))
    plan = campaign._shadow_plan(context, options, shadow)
    retrieved: set[str] = set()
    niche = campaign._niche_asset_count(shadow)
    feedback, reviews = campaign._run_council(context, shadow, options, plan, retrieved, niche)

    # The v1 contract is untouched: concept_verdicts is still the whole of what
    # the review decides. doctrine_version is an audit stamp the server writes
    # after validation (doctrine §8), never something the model produces.
    assert isinstance(feedback, Feedback)
    assert set(Feedback.model_fields) == {"concept_verdicts", "doctrine_version"}
    from app.agents.council import doctrine_version
    assert feedback.doctrine_version == doctrine_version()
    # one reviewer, so no seat reviews at all unless a stakeholder seat is added
    assert reviews == []

    family = objective_family(shadow.objective)
    required = set(ccs_mod.applicable_elements(family))
    assert {v.concept_id for v in feedback.concept_verdicts} == {c.id for c in plan.concepts}
    for verdict in feedback.concept_verdicts:
        assert {e.element for e in verdict.element_verdicts} >= required
        # unconditional now: a doctrine reviewer scans no corpus, so no asset
        # count earns a real saturation answer
        assert verdict.lenses.saturation.insufficient_data is True
        assert verdict.ccs_final == ccs_mod.compute_ccs(family, ccs_mod.final_ratings_of(verdict))
    # the v1 validator accepts it untouched — no campaign-shaped escape hatch
    assert validate_feedback(feedback, plan, shadow, campaign.rag,
                             retrieved_ids=retrieved, niche_asset_count=niche) is feedback

    # the review is in the audit trail, not just in the reviewer's head — one row
    # per option, each naming the doctrine that produced it (doctrine §8)
    logged = [a["detail"] for a in store.get_artifact_activity(tid, "council")]
    assert logged, "the review left no audit row"
    assert all(d.startswith("reviewer [doctrine ") for d in logged)
    assert {oid for oid in campaign._ws(tid)["options"]} <= {
        d.split("] ", 1)[1].split(":", 1)[0] for d in logged}


def test_a_truncated_agent_response_is_never_parsed_as_if_complete(monkeypatch):
    """Extended-thinking tokens share the output budget, so an over-long answer
    comes back as a *valid prefix* of JSON. Parsing it blames syntax and hides
    the real cause — which cost a full real-mode council run to diagnose. The
    runner must name the overflow instead, and keep the body that failed."""
    from app.agents import runner

    class _Details:
        thinking_tokens = 9000

    class _Usage:
        output_tokens = 16000
        output_tokens_details = _Details()

    class _Block:
        type = "text"
        # a complete prefix of a real Feedback object — parses as a delimiter error
        text = '{"concept_verdicts": [{"concept_id": "o1", "element_verdicts": [{"reason": "the brand seat flagged'

    class _Resp:
        stop_reason = "max_tokens"
        content = [_Block()]
        usage = _Usage()

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return _Resp()

    class _Messages:
        def stream(self, **kwargs):
            return _Stream()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: _Client())

    with pytest.raises(runner.AgentTruncated) as excinfo:
        runner._llm_call("claude-sonnet-4-6", "sys", [{"role": "user", "content": "x"}], None, False)

    message = str(excinfo.value)
    assert "incomplete, not invalid" in message      # honest about WHICH failure it is
    assert "thinking" in message                     # names the budget contention
    assert "delimiter" not in message                # never reported as a syntax bug
    # and it is retryable (ValueError) so the loop re-asks for a shorter answer
    assert isinstance(excinfo.value, ValueError)


def test_a_long_answer_split_across_text_blocks_is_not_silently_halved(monkeypatch):
    """`next(...)` took only the FIRST text block; a long council verdict that
    arrives in two blocks would have been truncated by the runner itself."""
    from app.agents import runner

    class _B:
        def __init__(self, text):
            self.type = "text"
            self.text = text

    class _Resp:
        stop_reason = "end_turn"
        content = [_B('{"a": 1,'), _B(' "b": 2}')]
        usage = None

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return _Resp()

    class _Messages:
        def stream(self, **kwargs):
            return _Stream()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: _Client())

    text = runner._llm_call("claude-sonnet-4-6", "sys", [{"role": "user", "content": "x"}], None, False)
    assert json.loads(text) == {"a": 1, "b": 2}


def test_the_planner_prompt_quotes_the_same_receipt_lexicon_the_validator_enforces():
    """R2 is a literal substring check. When the prompt only described it in
    prose the planner wrote storylines that satisfied the *intent* ("camera
    holds on the screen") and failed the *check* — a real-mode-only bug that
    burned a full run. The lexicon is injected from validators so the prompt
    and the check cannot drift apart."""
    from app.agents.runner import build_system
    from app.validators import _RECEIPT_CUES

    replacements = {"receipt_cues": ", ".join(f'"{c}"' for c in _RECEIPT_CUES)}
    system, version = build_system("campaign_planner", replacements)

    assert "{receipt_cues}" not in system          # substituted, not left dangling
    for cue in _RECEIPT_CUES:                      # every cue the check accepts is stated
        assert f'"{cue}"' in system, f"{cue!r} enforced but never shown to the planner"
    # and the prompt says the quiet part: intent is not enough
    assert "LITERALLY" in system
    assert version == "1.3.0"


def test_a_storyline_that_only_gestures_at_proof_still_fails_r2():
    """Guards the floor the prompt fix stands on — the fix is to the PROMPT,
    never to the check. A camera move must remain a failure."""
    from app.validators import _RECEIPT_CUES

    gesturing = "the camera holds on the monitor as the glare visibly shrinks to warm light"
    assert not any(cue in gesturing.lower() for cue in _RECEIPT_CUES)

    receipted = "a split screen shows the same monitor before and after, with a -40% callout"
    assert any(cue in receipted.lower() for cue in _RECEIPT_CUES)


def test_the_policy_document_is_optional_and_confirming_zero_claims_is_allowed():
    """The Brand Policy Document is optional, so a brand whose product text has
    nothing extractable must still be able to finish the Brand card. Confirming
    an EMPTY list is a legitimate answer, not a loophole: with no approved
    claim, every persuasion claim is unmapped and the council kill-flags it —
    which is the invariant working harder, not weaker."""
    campaign_id = campaign.start_campaign("No policy doc")["campaign_id"]

    # no policy_upload_id anywhere, and an empty confirmed list
    _seed_block(campaign_id, "brand", {
        "palette": ["#111111", "#222222"], "font": "Inter", "tagline": "t",
        "approved_claims": [], "banned_words": [], "claims_confirmed": True,
    })
    record = main.get_campaign(campaign_id)
    assert record["context"]["brand"]["policy_upload_id"] is None
    assert record["context_filled"]["brand"] is True          # the card completes

    # extraction without a policy doc still runs and says what it did
    out = main.claims_extract(campaign_id)
    assert any("policy" in note.lower() for note in out["notes"])

    # and the campaign is startable once the other two cards are filled
    _seed_block(campaign_id, "product", PRODUCT)
    _seed_block(campaign_id, "campaign", CAMPAIGN)
    assert campaign.missing_blocks(main.get_campaign(campaign_id)["context"]) == []


def test_an_unmapped_claim_still_kill_flags_when_the_approved_list_is_empty():
    """The floor the previous test stands on: confirming zero claims must not
    become a way to smuggle claims through unchecked."""
    campaign_id = campaign.start_campaign("Empty list floor")["campaign_id"]
    _seed_block(campaign_id, "brand", {
        "palette": ["#111111", "#222222"], "font": "Inter", "tagline": "t",
        "approved_claims": [], "banned_words": [], "claims_confirmed": True,
    })
    _seed_block(campaign_id, "product", PRODUCT)
    _seed_block(campaign_id, "campaign", CAMPAIGN)
    context = CampaignContext.model_validate(main.get_campaign(campaign_id)["context"])
    assert context.brand.approved_claims == []

    # a detail that states ANY claim is rejected, because none is mapped
    detail = CampaignDetail(
        creative_type=context.campaign.creative_type,
        shots=[{"slot": "slide_01", "duration_s": None,
                "visual_prompt": "a split screen of the product", "vo_or_copy": "see it"}],
        copy_primary="c", cta="Shop",
        claims_used=["Independently tested to cut glare by 40%"],
        style_ref=None, version=1, changes=[],
    )
    with pytest.raises(AgentValidationError) as exc:
        campaign._validate_detail(detail, context, None)
    assert "NOT in the confirmed approved_claims" in str(exc.value)


def test_real_mode_without_a_key_is_refused_up_front_not_discovered_mid_run(monkeypatch):
    """MOCK_LLM=0 with no ANTHROPIC_API_KEY is a broken deployment. It must be
    named at the door — /health and the start route — instead of dying deep in
    the SDK on the first agent call, which reads as a generic 500."""
    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(config, "LLM_KEY_PRESENT", False)

    reason = config.llm_unavailable_reason()
    assert reason and "ANTHROPIC_API_KEY" in reason
    assert main.health()["llm_unavailable"] == reason

    campaign_id = campaign.start_campaign("No key")["campaign_id"]
    _seed_block(campaign_id, "product", PRODUCT)
    _seed_block(campaign_id, "campaign", CAMPAIGN)
    _seed_block(campaign_id, "brand", BRAND)
    with pytest.raises(HTTPException) as exc:
        main.start_campaign(campaign_id)
    assert exc.value.status_code == 503          # not a 500, and not a silent mock

    # with a key present the door is open again
    monkeypatch.setattr(config, "LLM_KEY_PRESENT", True)
    assert config.llm_unavailable_reason() is None
    assert main.health()["llm_unavailable"] is None


def test_the_rumination_graph_fans_the_seats_out_and_caps_refine_at_one_pass():
    """The shape itself is the invariant. In the sequential driver 'three blind
    seats' and 'one refine pass' were conventions a future edit could quietly
    break; as a graph they are edges and state, so they are checkable."""
    from app.graph import RuminationState, build_rumination_graph, RuminationDeps
    from app.agents.council import SEATS

    graph = build_rumination_graph(RuminationDeps(
        run_options=lambda *a, **k: None, run_council=lambda *a, **k: (None, []),
        build_plan=lambda *a, **k: None, flagged_ids=lambda *a, **k: set(),
        merge_feedback=lambda a, b: a, objective_family=lambda o: o,
        seat_runner=lambda **k: (None, set()),
    ))
    drawn = graph.get_graph()
    edges = {(e.source, e.target) for e in drawn.edges}
    nodes = set(drawn.nodes)

    for seat in SEATS:
        node = f"seat_{seat}"
        assert node in nodes
        assert ("plan_options", node) in edges      # fanned out from ONE node…
        assert (node, "chair") in edges             # …and joined at the chair
        # blindness as a graph property: no seat feeds another seat
        for other in SEATS:
            if other != seat:
                assert (node, f"seat_{other}") not in edges

    # the refine pass may be entered, but never re-entered
    assert ("refine", "chair") not in edges
    assert ("refine", "refine") not in edges


def test_refine_cannot_run_twice_even_if_options_stay_flagged():
    """The one-pass cap lives in state (`refine_done`), so an option that is
    STILL flagged after refining ends the run instead of looping forever."""
    from app.graph import RuminationState, build_rumination_graph, RuminationDeps

    calls = {"refine": 0}

    def _refine_council(*a, **k):
        return Feedback(concept_verdicts=[]), []

    def _opts():
        return CampaignOptions(options=[
            CampaignOption(option_id=f"o{i}", name_line=f"Angle {i}",
                           description="d", storyline="a split screen shows the proof",
                           objective_echo="conversions", why_it_fits="no evidence in DB",
                           evidence=[])
            for i in (1, 2)
        ])

    def _run_options(*a, **k):
        if k.get("previous") is not None:
            calls["refine"] += 1
        return _opts()

    graph = build_rumination_graph(RuminationDeps(
        run_options=_run_options,
        run_council=_refine_council,
        build_plan=campaign._shadow_plan,          # the real one — no fake Plan to keep valid
        flagged_ids=lambda f, fb: {"o1"},          # ALWAYS flagged — the pathological case
        merge_feedback=lambda a, b: a,
        objective_family=lambda o: o,
        # a REAL SeatReview: RuminationState validates every value that crosses
        # an edge, so a None here is rejected before it can reach the chair
        seat_runner=lambda **k: (
            SeatReview(seat=k["seat"], element_scores=[
                SeatScore(element="hook_strength", rating="L", reason="r", evidence=[])
            ]), set()),
    ))
    final = graph.invoke(RuminationState(
        context=CampaignContext.model_validate({"name": "n", "product": PRODUCT,
                                                "campaign": CAMPAIGN, "brand": BRAND}),
        shadow=campaign._shadow_context(
            CampaignContext.model_validate({"name": "n", "product": PRODUCT,
                                            "campaign": CAMPAIGN, "brand": BRAND})),
    ))
    state = final if isinstance(final, RuminationState) else RuminationState(**final)
    assert calls["refine"] == 1          # exactly one refine, never two
    assert state.refine_done is True


def test_the_reviewer_sees_stakeholder_seats_in_canonical_order(tmp_path, monkeypatch):
    """Stakeholder seats EXECUTE concurrently, so they finish in whatever order
    the models answer. The reviewer is an LLM and LLMs are order-sensitive, so an
    unsorted join would make the same input produce a different review
    run-to-run. The review node sorts to the canonical roster order — parallel
    speed, deterministic input.

    With no stakeholder seats (the default) there is no join to make
    deterministic, which is one more thing the single reviewer removed."""
    from app import seats as seats_mod

    _stakeholder_prompts(tmp_path, monkeypatch,
                         {"my_cmo": "SEAT — the client's CMO.",
                          "legal": "SEAT — client legal."})
    roster = seats_mod.resolve(["my_cmo", "legal"])
    order = seats_mod.slugs(roster)
    assert order == ["my_cmo", "legal"]

    from app.graph import RuminationDeps, build_rumination_graph
    graph = build_rumination_graph(RuminationDeps(
        run_options=lambda *a, **k: None, run_council=lambda *a, **k: (None, []),
        build_plan=lambda *a, **k: None, flagged_ids=lambda *a, **k: set(),
        merge_feedback=lambda a, b: a, objective_family=lambda o: o,
        seat_runner=lambda **k: (None, set()),
    ), seats=order)
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    for slug in order:
        assert ("plan_options", f"seat_{slug}") in edges
        assert (f"seat_{slug}", "review") in edges


def test_one_product_image_is_enough_and_claims_do_not_block_the_start():
    """Two gates relaxed on purpose. A single pack shot locks consistency, and
    confirming claims GRANTS permission to make them rather than tolling the
    start. Neither relaxation may touch what the campaign is allowed to SAY."""
    campaign_id = campaign.start_campaign("One image, no claims")["campaign_id"]
    _seed_block(campaign_id, "product", {
        "name": "Solo", "description": "one shot is plenty", "image_upload_ids": ["up_1"]})
    _seed_block(campaign_id, "campaign", CAMPAIGN)
    _seed_block(campaign_id, "brand", {
        "palette": ["#111111", "#222222"], "font": "Inter", "tagline": "t",
        "approved_claims": [], "banned_words": [], "claims_confirmed": False})

    record = main.get_campaign(campaign_id)
    assert record["context_filled"] == {"product": True, "campaign": True, "brand": True}
    assert campaign.missing_blocks(record["context"]) == []      # startable
    assert main.start_campaign(campaign_id) == {"ok": True}


def test_an_unconfirmed_claims_list_is_candidates_not_permissions():
    """The floor the relaxation stands on. Dropping the start gate must NOT let
    a saved-but-unconfirmed list act as approved — otherwise the extractor would
    be granting itself authority."""
    campaign_id = campaign.start_campaign("Candidates only")["campaign_id"]
    _seed_block(campaign_id, "product", PRODUCT)
    _seed_block(campaign_id, "campaign", CAMPAIGN)
    saved = _seed_block(campaign_id, "brand", {
        "palette": ["#111111", "#222222"], "font": "Inter", "tagline": "t",
        "approved_claims": ["Cuts glare by 40%"], "banned_words": [],
        "claims_confirmed": False})
    assert saved["brand"]["approved_claims"] == ["Cuts glare by 40%"]
    assert saved["brand"]["claims_confirmed"] is False

    context = CampaignContext.model_validate(saved)
    detail = CampaignDetail(
        creative_type=context.campaign.creative_type,
        shots=[{"slot": "slide_01", "duration_s": None,
                "visual_prompt": "a split screen of the product", "vo_or_copy": "see it"}],
        copy_primary="c", cta="Shop",
        claims_used=["Cuts glare by 40%"],          # present, but NOT confirmed
        style_ref=None, version=1, changes=[])
    with pytest.raises(AgentValidationError) as exc:
        campaign._validate_detail(detail, context, None)
    assert "NOT in the confirmed approved_claims" in str(exc.value)


def test_the_test_suite_never_writes_to_the_real_agent_run_log():
    """Tests drive agents into deliberate failure. Those runs must not land in
    data/runs/agent_runs.jsonl — the observability view reads that file, and a
    fixture's invalid output showing up there reads as a production incident.
    (It did: 23 'campaign_planner.test' failures accumulated before this.)"""
    from app import config as cfg

    real = Path(__file__).resolve().parent.parent / "data" / "runs"
    assert Path(cfg.LOG_DIR).resolve() != real.resolve(), (
        "LOG_DIR is not isolated — this run is appending to the real agent log"
    )


def test_a_brief_typed_at_the_first_step_is_taken_not_refused():
    """The user typed their whole brief and got 'Didn't catch a campaign
    command.' That was the original bug at the two-path card; the paths are
    gone, but the rule they taught is now the entire front door, so it matters
    more, not less: whatever the user types first IS the brief.
    """
    campaign_id = campaign.start_campaign("Brief first")["campaign_id"]
    thread_id = campaign._campaign_thread_id(campaign_id)
    ws = campaign._ws(thread_id)

    parsed = campaign._parse("intake", "generate campaign for this product which is for parties", ws)
    assert parsed is not None, "a real brief was refused at the first step"
    assert parsed["event"] == "intake"
    assert parsed["extra"] == "generate campaign for this product which is for parties", (
        "the user's own words must reach the agent verbatim, not a parsed shadow of them")

    # "start" is still the one word that means start, not a thing to file
    assert campaign._parse("intake", "start", ws)["event"] == "begin"


def test_an_attached_image_survives_until_there_is_a_product_to_put_it_on():
    """Attachments used to be stringified into the message ('[attached images:
    upl_x]'), so the agent saw an upload id as prose and nothing told it what to
    do — the file was silently dropped. They are data now, and the server
    applies them itself rather than trusting the model to copy them."""
    campaign_id = campaign.start_campaign("Attachment survives")["campaign_id"]
    thread_id = campaign._campaign_thread_id(campaign_id)

    # message 1: an image, but nothing that can build a product block yet
    campaign.handle_event(UserEvent(thread_id=thread_id, type="text",
                                    text="a campaign for this, it is for parties",
                                    upload_ids=["upl_held"]))
    assert campaign._ws(thread_id)["pending_uploads"] == ["upl_held"]

    # later, a product exists — the held id is applied exactly once and cleared
    ctx = CampaignContext.model_validate({
        "name": "Attachment survives",
        "product": {"name": "Aera", "description": "d", "image_upload_ids": []},
        "campaign": None, "brand": None})
    applied = campaign._apply_pending_uploads(thread_id, ctx)
    assert applied.product.image_upload_ids == ["upl_held"]
    assert campaign._ws(thread_id)["pending_uploads"] == []

    # and it is not applied twice
    again = campaign._apply_pending_uploads(thread_id, applied)
    assert again.product.image_upload_ids == ["upl_held"]


# ================================================================================
# v3 §7 — the frozen council doctrine
# The council stopped retrieving and started judging. Everything below pins the
# part of that which a prompt cannot enforce on its own.
# ================================================================================


def _seat_review(seat: str = "performance", **over) -> dict:
    """A minimal well-formed seat output, doctrine-shaped."""
    base = {
        "seat": seat,
        "element_scores": [{
            "element": "hook_strength", "rating": "M", "reason": "the opener states a stake",
            "evidence": [{"tag": "PRINCIPLE", "source_id": "model",
                          "claim": "an opening earns attention", "as_of": None}],
        }],
        "kill_recommendation": None,
        "policy_check_required": False,
        "policy_notes": [],
        "fixes": [],
    }
    base.update(over)
    return base


def _feedback(**lens_over) -> Feedback:
    lenses = {
        "saturation": {"similar_count": 0, "source_id": None,
                       "note": "judged from doctrine, not a corpus scan",
                       "insufficient_data": True},
        "claims_safety": "no unmapped claim", "feasibility": "producible",
        "platform_policy": "no assertion made", "policy_check_required": False,
    }
    lenses.update(lens_over)
    return Feedback.model_validate({"concept_verdicts": [{
        "concept_id": "o1",
        "element_verdicts": [{"element": "hook_strength", "verdict": "agree",
                              "final_rating": "M", "reason": "brand seat: reads clean",
                              "evidence": [], "evidence_gap": True}],
        "lenses": lenses, "kill_flags": [], "fixes": [], "ccs_final": 0,
    }]})


def _spec(slug: str = "performance", can_kill: bool = True):
    from app.seats import SeatSpec
    return SeatSpec(slug=slug, prompt_name=f"council/seat_{slug}",
                    can_kill=can_kill, builtin=True)


def test_a_council_pass_makes_zero_retrieval_calls():
    """The doctrine change is only real if the tools are actually gone. Dropping
    the dispatcher alone would NOT do it: run_agent attaches TOOL_DEFS
    independently, so a seat would still call tools and get
    {"error": "no tools available"} back — retrieval removed in spirit, tool-loop
    turns burned in fact."""
    from app.agents import council as council_mod

    real = council_mod.run_agent
    calls: list[dict] = []

    def spy(**kwargs):
        if kwargs.get("agent", "").startswith("council."):
            calls.append(kwargs)
        return real(**kwargs)

    council_mod.run_agent = spy
    try:
        _ruminated("Zero retrieval")
    finally:
        council_mod.run_agent = real

    assert calls, "no council agent ran"
    for kwargs in calls:
        assert kwargs["dispatcher"] is None, f"{kwargs['agent']} was handed a dispatcher"
        assert kwargs["use_tools"] is False, f"{kwargs['agent']} still had tools attached"
        assert kwargs["preludes"] == ["council/doctrine"]


def test_every_council_citation_is_a_principle():
    """A reviewer with no corpus has nothing to cite but its own judgment, and
    must say so in the tag rather than borrowing an id the planner retrieved."""
    cid, tid = _ruminated("Principle only")
    context = campaign._context_of(cid)
    shadow = campaign._shadow_context(context)
    options = CampaignOptions(options=list(campaign._ws(tid)["options"].values()))
    plan = campaign._shadow_plan(context, options, shadow)
    feedback, reviews = campaign._run_council(
        context, shadow, options, plan, set(), campaign._niche_asset_count(shadow))

    cited = [e for r in reviews for s in r.element_scores for e in s.evidence]
    cited += [e for v in feedback.concept_verdicts
              for ev in v.element_verdicts for e in ev.evidence]
    assert cited, "the council cited nothing at all"
    for item in cited:
        assert item.tag == "PRINCIPLE" and item.source_id == "model"

    # and the doctrine that produced them is recorded on both, for the audit
    from app.agents.council import doctrine_version
    live = doctrine_version()
    assert live and live != "unversioned", "the doctrine must carry a version header"
    assert all(r.doctrine_version == live for r in reviews)
    assert feedback.doctrine_version == live

    # the DURABLE audit row carries it too. The agent-run log also records it,
    # but that surface is gated on PLOTLINE_DEBUG_OBSERVABILITY and off in
    # production, so it cannot be the audit trail.
    activity = store.get_artifact_activity(tid, "council")
    assert activity, "council left no audit row"
    assert any(f"doctrine {live}" in row["detail"] for row in activity)


def test_a_chair_citing_a_retrieved_id_is_rejected():
    """The planner's ids are still in scope when the chair runs. Without this the
    doctrine would be prompt-deep: a chair citing chunk:C0421 would validate."""
    from app.validators import validate_council

    borrowed = _feedback()
    borrowed.concept_verdicts[0].element_verdicts[0].evidence = [
        Evidence(tag="REF", source_id="chunk:C0421",
                 claim="borrowed from the planner", as_of=None)
    ]
    with pytest.raises(AgentValidationError) as exc:
        validate_council(borrowed)
    assert "PRINCIPLE" in str(exc.value)


def test_the_saturation_lens_always_declares_insufficient_data():
    """Saturation is a measurement over a corpus. validate_feedback only forced
    this BELOW a niche-asset threshold, which implied a big enough corpus would
    earn a real answer. Under the doctrine no count does."""
    from app.validators import validate_council

    with pytest.raises(AgentValidationError) as exc:
        validate_council(_feedback(saturation={
            "similar_count": 0, "source_id": None, "note": "looks fresh",
            "insufficient_data": False}))
    assert "insufficient_data" in str(exc.value)

    # …and it cannot report a count either, having scanned nothing
    with pytest.raises(AgentValidationError):
        validate_council(_feedback(saturation={
            "similar_count": 12, "source_id": None, "note": "n",
            "insufficient_data": True}))


def test_the_doctrine_file_states_no_benchmark():
    """The doctrine is the council's whole knowledge base, so a number in it is a
    fabrication every seat would inherit."""
    from app.validators import _check_no_benchmarks

    text = (config.PROMPTS_DIR / "council" / "doctrine.md").read_text()
    errors: list[str] = []
    for line in text.splitlines():
        _check_no_benchmarks(line, "doctrine", errors)
    assert errors == [], f"doctrine states a benchmark: {errors}"


def test_an_invented_benchmark_in_a_seat_reason_is_rejected():
    from app.validators import validate_seat_review

    for bad in ("retention drops 40% after the third beat",
                "this lifts CTR",
                "the hook must land in the first 3 seconds",
                "delivers 3x more saves"):
        review = SeatReview.model_validate(_seat_review(element_scores=[{
            "element": "hook_strength", "rating": "L", "reason": bad,
            "evidence": [{"tag": "PRINCIPLE", "source_id": "model",
                          "claim": "openers matter", "as_of": None}]}]))
        with pytest.raises(AgentValidationError):
            validate_seat_review(review, _spec())


def test_a_quoted_claim_is_not_mistaken_for_an_invented_benchmark():
    """D8 ORDERS the Brand seat to quote the offending phrase when it flags an
    unmapped claim — so the most important thing this council catches arrives
    with a number inside it by design. Scanning the quote rejected the seat for
    doing its job and hard-failed the campaign on the compliance path
    specifically. Quoted spans are attributed to the draft, not to the council."""
    from app.validators import validate_seat_review

    review = SeatReview.model_validate(_seat_review(
        seat="brand",
        element_scores=[{
            "element": "persuasion_proof", "rating": "L",
            "reason": "the line '3x faster than QuickBooks' has no approved claim behind it",
            "evidence": [{"tag": "PRINCIPLE", "source_id": "model",
                          "claim": "an unmapped claim is a liability", "as_of": None}]}],
        kill_recommendation="unsubstantiated_claim: '3x faster than QuickBooks'"))
    assert validate_seat_review(review, _spec("brand")) is review


def test_a_seat_cannot_rate_with_no_evidence_at_all():
    """SeatScore.evidence has no min_length, so 'every citation is a PRINCIPLE'
    would otherwise pass by citing nothing."""
    from app.validators import validate_seat_review

    review = SeatReview.model_validate(_seat_review(element_scores=[{
        "element": "hook_strength", "rating": "H", "reason": "good", "evidence": []}]))
    with pytest.raises(AgentValidationError) as exc:
        validate_seat_review(review, _spec())
    assert "no evidence" in str(exc.value)


def test_the_platform_seat_flags_a_policy_check_instead_of_stating_the_rule():
    """Inventing policy text is the worst failure mode this product has. The seat
    names what a human must check; it never answers the question."""
    from app.validators import validate_council, validate_seat_review

    review = SeatReview.model_validate(_seat_review(
        seat="platform", policy_check_required=True,
        policy_notes=["before/after imagery needs a check against Meta's current ad rules"]))
    assert validate_seat_review(review, _spec("platform")) is review

    # and the refusal must survive consolidation, or QC never learns it is owed
    with pytest.raises(AgentValidationError) as exc:
        validate_council(_feedback(), seat_reviews=[review])
    assert "policy_check_required" in str(exc.value)
    validate_council(_feedback(policy_check_required=True), seat_reviews=[review])


def test_a_kill_flag_is_never_dropped_in_silence():
    from app.validators import validate_council

    killer = SeatReview.model_validate(
        _seat_review(seat="brand", kill_recommendation="unsubstantiated_claim: 'seamless'"))
    with pytest.raises(AgentValidationError) as exc:
        validate_council(_feedback(), seat_reviews=[killer])
    assert "never drop it" in str(exc.value)


def test_the_doctrine_reaches_every_seat_and_the_chair():
    """Prompt-and-check drift is the bug class that cost this project a paid run
    before (the R2 lexicon). The doctrine is injected, so assert it ARRIVES."""
    from app.agents.council import REVIEWER_PROMPT, doctrine_version
    from app.agents.runner import build_system
    from app.seats import BUILTIN_SEATS

    for name in [f"council/seat_{s}" for s in BUILTIN_SEATS] + [REVIEWER_PROMPT]:
        system, version = build_system(name, None, ["council/doctrine"])
        # the persona, the beliefs, and the red lines all arrived
        assert "would I put my name on this going live tomorrow" in system
        assert "D8 — Claims are a liability surface" in system
        assert "unsubstantiated_claim" in system
        # ordering is load-bearing: the doctrine NARROWS shared_policy (whose
        # rule 2 demands a retrieved source_id a doctrine seat can never produce),
        # so it has to come after it and before the seat's own lens.
        assert system.index("EVIDENCE & HONESTY POLICY") < system.index("COUNCIL DOCTRINE")
        assert system.index("COUNCIL DOCTRINE") < system.index("OUTPUT SHAPE")
        # the composite version answers "which reviewer said this"
        assert version.endswith(f"+doctrine@{doctrine_version()}")


# ------------------------------------------------- stakeholder seats (§7 v3) --


def _stakeholder_prompts(tmp_path: Path, monkeypatch, extra: dict[str, str]) -> None:
    """A prompts dir with the real council plus some stakeholder seats."""
    council = tmp_path / "council"
    council.mkdir(parents=True)
    for path in (config.PROMPTS_DIR / "council").glob("*.md"):
        (council / path.name).write_text(path.read_text())
    for slug, body in extra.items():
        (council / f"seat_{slug}.md").write_text(body)
    monkeypatch.setattr(config, "PROMPTS_DIR", tmp_path)


def test_a_stakeholder_seat_joins_the_council_without_a_schema_change(tmp_path, monkeypatch):
    """The agency sale: a seat for the CLIENT's reviewer. SeatReview.seat was a
    three-value Literal, so this could not previously validate at all."""
    from app import seats as seats_mod

    _stakeholder_prompts(tmp_path, monkeypatch, {"my_cmo": "SEAT — the client's CMO."})
    roster = seats_mod.resolve(["my_cmo"])

    # the reviewer is not a seat, so a campaign's roster is exactly its guests
    assert seats_mod.slugs(roster) == ["my_cmo"]
    assert SeatReview.model_validate(_seat_review(seat="my_cmo")).seat == "my_cmo"

    # …and it becomes exactly one more node on the graph, nothing else
    from app.graph import RuminationDeps, build_rumination_graph
    graph = build_rumination_graph(RuminationDeps(
        run_options=lambda *a, **k: None, run_council=lambda *a, **k: (None, []),
        build_plan=lambda *a, **k: None, flagged_ids=lambda *a, **k: set(),
        merge_feedback=lambda a, b: a, objective_family=lambda o: o,
        seat_runner=lambda **k: (None, set()),
    ), seats=seats_mod.slugs(roster))
    drawn = graph.get_graph()
    edges = {(e.source, e.target) for e in drawn.edges}
    assert "seat_my_cmo" in set(drawn.nodes)
    assert ("plan_options", "seat_my_cmo") in edges and ("seat_my_cmo", "review") in edges
    # with a guest present the planner no longer feeds the reviewer directly —
    # the guest is in between, and the reviewer judges it
    assert ("plan_options", "review") not in edges


def test_a_stakeholder_seat_cannot_kill_unless_its_file_grants_it(tmp_path, monkeypatch):
    """Compliance authority stays with the Marketing Expert by default. The
    failure mode is a client's guest reviewer silently killing a campaign."""
    from app import seats as seats_mod
    from app.validators import validate_seat_review

    _stakeholder_prompts(tmp_path, monkeypatch, {
        "my_cmo": "SEAT — the client's CMO.",
        "legal": "SEAT — client legal.\ncan_kill: true",
    })
    by_slug = {s.slug: s for s in seats_mod.resolve(["my_cmo", "legal"])}
    assert by_slug["my_cmo"].can_kill is False
    assert by_slug["legal"].can_kill is True

    killing = SeatReview.model_validate(
        _seat_review(seat="my_cmo", kill_recommendation="policy_risk: I don't like it"))
    with pytest.raises(AgentValidationError) as exc:
        validate_seat_review(killing, by_slug["my_cmo"])
    assert "does not hold one" in str(exc.value)

    granted = SeatReview.model_validate(
        _seat_review(seat="legal", kill_recommendation="policy_risk: unlicensed music bed"))
    assert validate_seat_review(granted, by_slug["legal"]) is granted


def test_the_seat_cap_holds_and_an_unknown_seat_is_refused(tmp_path, monkeypatch):
    from app import seats as seats_mod
    from app.seats import SeatConfigError

    _stakeholder_prompts(tmp_path, monkeypatch,
                         {f"guest{i}": f"SEAT — guest {i}." for i in range(6)})
    with pytest.raises(SeatConfigError) as exc:
        seats_mod.resolve([f"guest{i}" for i in range(6)])
    assert "cap" in str(exc.value)

    with pytest.raises(SeatConfigError) as exc:
        seats_mod.resolve(["nobody_home"])
    assert "no prompt at" in str(exc.value)


def test_campaign_settings_default_to_a_single_reviewer():
    """Defaults must reproduce today's behaviour exactly — an absent settings row
    can never mean an absent capability. After the owner's simplification that
    default is ONE Marketing Expert and no seats at all."""
    from app.seats import slugs

    cid, _ = _filled("Default council")
    assert store.get_campaign_settings(cid) == {}
    assert slugs(campaign._campaign_seats(cid)) == []

    store.update_campaign_settings(cid, {"seats": []})
    store.update_campaign_settings(cid, {"unrelated": 1})
    assert store.get_campaign_settings(cid) == {"seats": [], "unrelated": 1}   # merge, not clobber


def test_progress_labels_follow_the_thread_that_is_running(monkeypatch):
    """The graph is compiled once and cached. on_step used to CLOSE OVER
    thread_id, so every campaign after the first wrote its progress labels onto
    the FIRST campaign's thread — 'labelled working steps, never a bare spinner'
    held on campaign one and quietly broke on campaign two."""
    from app.agents.runner import current_thread

    captured: list = []
    real = campaign.build_rumination_graph

    def spy(deps, seats=None):
        captured.append(deps)
        return real(deps, seats=seats)

    monkeypatch.setattr(campaign, "build_rumination_graph", spy)
    campaign._RUMINATION_GRAPHS.clear()
    _ruminated("Thread labels")
    assert captured, "graph was never built"

    on_step = captured[0].on_step
    current_thread.set("thr_second_campaign")
    on_step("drafting options")
    assert campaign._working["thr_second_campaign"] == "drafting options"


def test_a_dropped_connection_retries_instead_of_discarding_the_run(monkeypatch):
    """A transport error used to escape run_agent entirely — no log row (so the
    node vanished from observability) and the whole rumination discarded because
    one mid-stream blip hit the last node. On the production models that is
    minutes of already-paid, already-successful work thrown away.

    It is not a validation failure and must never be reported to the model as
    one: there was no output to correct."""
    from app.agents import runner as runner_mod

    calls = {"n": 0}
    sent: list = []

    def flaky(model, system, messages, dispatcher, use_tools, **kw):
        calls["n"] += 1
        sent.append(messages)
        if calls["n"] == 1:
            raise runner_mod.AgentTransport("peer closed connection mid-stream")
        return json.dumps(_feedback().model_dump(mode="json"))

    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(runner_mod, "_llm_call", flaky)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    review, log = runner_mod.run_agent(
        agent="council.marketing_expert", prompt_name="council/marketing_expert",
        model="test-model", user_payload={"stage": "campaign_options"},
        schema=Feedback, preludes=["council/doctrine"],
        dispatcher=None, use_tools=False)

    assert isinstance(review, Feedback) and calls["n"] == 2 and log.attempts == 2

    # the retry re-issued the request VERBATIM. Telling the model its answer
    # "failed validation" would make it rewrite a good answer it never sent.
    assert len(sent[1]) == 1, "a corrective message was appended after a transport drop"
    assert "failed validation" not in json.dumps(sent[1], default=str)

    # …and the node is in the run log, not missing from observability
    rows = [json.loads(l) for l in
            (config.LOG_DIR / "agent_runs.jsonl").read_text().splitlines() if l.strip()]
    mine = [r for r in rows if r["agent"] == "council.marketing_expert"]
    assert mine and mine[-1]["attempts"] == 2
    assert any("connection dropped" in e or "peer closed" in e
               for e in mine[-1]["validation_errors"])


def test_an_api_status_error_is_not_retried_as_a_blip(monkeypatch):
    """Connection-class errors are transient. A 401/403/400 is a real answer from
    the server and must surface immediately rather than spin the retry loop."""
    from app.agents import runner as runner_mod

    calls = {"n": 0}

    def refused(model, system, messages, dispatcher, use_tools, **kw):
        calls["n"] += 1
        raise RuntimeError("401 authentication_error")

    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(runner_mod, "_llm_call", refused)

    with pytest.raises(RuntimeError):
        runner_mod.run_agent(
            agent="council.marketing_expert", prompt_name="council/marketing_expert",
            model="test-model", user_payload={}, schema=Feedback,
            preludes=["council/doctrine"], dispatcher=None, use_tools=False)
    assert calls["n"] == 1, "a non-transient error was retried"


# ================================================================================
# v3 §4 — the settings model. A gate mode decides whether the flow PAUSES; it
# never decides whether the artifact is PRODUCED.
# ================================================================================


def test_the_default_policy_reproduces_todays_behaviour():
    from app.schemas import GATEABLE_STAGES, ReviewPolicy

    policy = ReviewPolicy()
    assert all(policy.mode(s) == "review" for s in GATEABLE_STAGES)
    assert all(policy.pauses_at(s) for s in GATEABLE_STAGES)
    # every media count starts at 1 — nothing multiplies spend by default
    assert (policy.keyframes_per_shot, policy.takes_per_shot,
            policy.variants, policy.voice_candidates) == (1, 1, 1, 1)
    # a stage nobody can gate is always a pause: absent config can never mean
    # an absent gate
    assert policy.mode("generate") == "review" and policy.pauses_at("generate")


def test_a_cost_gate_cannot_be_configured_away():
    """§4.3 rule 1. model_confirm and the single-vs-variants question always
    precede generation. `generate` is not in GATEABLE_STAGES, and naming it is
    an ERROR rather than a silent no-op — a settings screen that appears to
    accept it would be lying."""
    from app.schemas import ReviewPolicy

    with pytest.raises(ValidationError) as exc:
        ReviewPolicy(gates={"generate": "auto"})
    assert "not gateable" in str(exc.value)


def test_qc_and_keyframes_can_never_be_skipped():
    """§4.3 rules 3 and 4. `auto` means 'do not stop for me to read it'; it never
    means 'do not check'. Animating an unapproved frame is the mistake the whole
    cost ladder exists to prevent."""
    from app.schemas import ReviewPolicy

    for stage in ("qc", "keyframes"):
        with pytest.raises(ValidationError) as exc:
            ReviewPolicy(gates={stage: "skip"})
        assert "cannot be skipped" in str(exc.value)
        # …but auto is legal for both: produced and audited, just not waited on
        assert ReviewPolicy(gates={stage: "auto"}).mode(stage) == "auto"


def test_skip_is_legal_only_where_the_trade_is_surfaced():
    """§4.3 rule 5. skip means the WORK is not done, so it is confined to the two
    stages that state what is being given up."""
    from app.schemas import SKIPPABLE_STAGES, ReviewPolicy

    assert SKIPPABLE_STAGES == {"templates", "canon"}
    for stage in SKIPPABLE_STAGES:
        assert ReviewPolicy(gates={stage: "skip"}).mode(stage) == "skip"
    for stage in ("brief", "options", "script", "detail", "creative"):
        with pytest.raises(ValidationError):
            ReviewPolicy(gates={stage: "skip"})


def test_the_presets_are_the_same_model_not_a_second_one():
    from app.schemas import POLICY_PRESETS, ReviewPolicy, policy_from_preset

    assert set(POLICY_PRESETS) == {"full_craft", "fast", "volume"}
    assert policy_from_preset("full_craft") == ReviewPolicy()          # the default
    fast = policy_from_preset("fast")
    assert fast.mode("brief") == "auto" and fast.mode("keyframes") == "review"
    volume = policy_from_preset("volume")
    assert volume.mode("keyframes") == "review" and volume.mode("qc") == "review"
    assert volume.takes_per_shot == 2 and volume.variants == 3
    with pytest.raises(ValueError):
        policy_from_preset("nope")


def test_settings_round_trip_over_http_and_merge_rather_than_clobber():
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    cid, _ = _filled("Settings")

    got = client.get(f"/api/campaigns/{cid}/settings").json()
    assert got["review_policy"]["gates"]["qc"] == "review"
    assert got["seats"] == [] and "full_craft" in got["presets"]

    # a preset writes the same ReviewPolicy
    got = client.patch(f"/api/campaigns/{cid}/settings", json={"preset": "volume"}).json()
    assert got["review_policy"]["takes_per_shot"] == 2

    # patching ONE count leaves the preset's gates alone
    got = client.patch(f"/api/campaigns/{cid}/settings",
                       json={"review_policy": {"variants": 2}}).json()
    assert got["review_policy"]["variants"] == 2
    assert got["review_policy"]["takes_per_shot"] == 2
    assert got["review_policy"]["gates"]["brief"] == "auto"   # still the volume preset

    # and the hard rules are enforced on WRITE, while the user is looking at it
    bad = client.patch(f"/api/campaigns/{cid}/settings",
                       json={"review_policy": {"gates": {"qc": "skip"}}})
    assert bad.status_code == 422 and "cannot be skipped" in bad.json()["detail"]
    bad = client.patch(f"/api/campaigns/{cid}/settings", json={"seats": ["ghost"]})
    assert bad.status_code == 422 and "no prompt at" in bad.json()["detail"]

    # the refused writes changed nothing
    assert client.get(f"/api/campaigns/{cid}/settings").json()["review_policy"]["gates"]["qc"] == "review"


def test_a_campaign_reads_its_own_policy_and_a_corrupt_one_fails_safe():
    cid, _ = _filled("Policy read")
    assert campaign._pauses_at(cid, "detail") is True

    store.update_campaign_settings(cid, {"review_policy": {"gates": {"detail": "auto"}}})
    assert campaign._pauses_at(cid, "detail") is False
    assert campaign._pauses_at(cid, "qc") is True            # untouched gates stay

    # an unreadable blob must not become a MORE permissive setting
    store.update_campaign_settings(cid, {"review_policy": {"gates": {"detail": "banana"}}})
    assert campaign._pauses_at(cid, "detail") is True


# ================================================================================
# v3 §1 — campaign_brief. The gate that asks "are we making the right ad?"
# ================================================================================


def _brief(**over) -> dict:
    base = {
        "objective": "conversions", "audience": CAMPAIGN["target_audience"],
        "platforms": CAMPAIGN["platforms"], "creative_type": "image",
        "target_metric": None,
        "audience_current_belief": "they think every invoicing app is the same",
        "single_message": "files GST in 60 seconds",
        "brand_role": "Ledger does the filing on screen while the timer runs",
        "offer_cta": "Start free",
        "aspect_ratios": ["9:16"], "languages": ["en-IN"],
        "proof_points": ["files GST in 60 seconds"],
    }
    base.update(over)
    return base


def test_the_brief_validates_ratios_against_the_same_table_as_the_ad_card():
    """ONE spec table. Two lists drift, and the second one is always the stale
    one — so the brief reads exactly what AdCard enforces."""
    from app.schemas import PLACEMENT_RATIOS

    assert CampaignBrief.model_validate(_brief(aspect_ratios=sorted(PLACEMENT_RATIOS)))
    with pytest.raises(ValidationError) as exc:
        CampaignBrief.model_validate(_brief(aspect_ratios=["3:2"]))
    assert "placement spec table" in str(exc.value)

    # and the AdCard rejects exactly the same set, from the same constant
    with pytest.raises(ValidationError):
        AdCard.model_validate({
            "id": "ad_1", "campaign_id": "c", "thread_id": "t", "option_id": "o1",
            "creative_type": "image", "placements": {}, "ratios": ["3:2"], "naming": "n"})


def test_a_proof_point_outside_the_confirmed_claims_is_refused():
    """A proof point IS a claim. It reaches the same rule as claims_used, through
    the same helper — two copies of a compliance rule is one copy plus a bug."""
    cid, _ = _filled("Brief claims")
    context = campaign._context_of(cid)

    ok = CampaignBrief.model_validate(_brief())
    assert campaign._validate_brief(ok, context) is ok

    bad = CampaignBrief.model_validate(_brief(proof_points=["3x faster than QuickBooks"]))
    with pytest.raises(AgentValidationError) as exc:
        campaign._validate_brief(bad, context)
    assert "3x faster than QuickBooks" in str(exc.value)
    assert "kill flag" in str(exc.value)


def test_unconfirmed_claims_are_candidates_not_permissions_for_the_brief():
    """The same trap the detail has: a saved-but-unconfirmed list would let the
    extractor grant itself authority."""
    cid, _ = _filled("Brief unconfirmed")
    context = campaign._context_of(cid)
    context.brand.claims_confirmed = False

    brief = CampaignBrief.model_validate(_brief())
    with pytest.raises(AgentValidationError):
        campaign._validate_brief(brief, context)


def test_the_brief_echoes_the_cards_and_cannot_re_decide_them():
    cid, _ = _filled("Brief echo")
    context = campaign._context_of(cid)

    for field, value in (("objective", "awareness"), ("creative_type", "video")):
        with pytest.raises(AgentValidationError) as exc:
            campaign._validate_brief(CampaignBrief.model_validate(_brief(**{field: value})), context)
        assert "does not re-decide" in str(exc.value) or "contradicts" in str(exc.value)

    # a platform the card never named cannot appear on the brief
    with pytest.raises(AgentValidationError) as exc:
        campaign._validate_brief(
            CampaignBrief.model_validate(_brief(platforms=["tiktok"])), context)
    assert "the campaign card does not" in str(exc.value)

    # …but a SUBSET is fine: the brief may narrow the card, never widen it
    narrowed = campaign._validate_brief(
        CampaignBrief.model_validate(_brief(platforms=["linkedin"])), context)
    assert narrowed.platforms == ["linkedin"]


def test_a_two_idea_message_warns_but_does_not_block():
    """D2 is a judgment call. Blocking on a heuristic would make the brief harder
    to produce than the ad — it surfaces on the card instead."""
    cid, _ = _filled("Brief warn")
    context = campaign._context_of(cid)

    brief = campaign._validate_brief(
        CampaignBrief.model_validate(
            _brief(single_message="files GST in 60 seconds and saves 4 hours")), context)
    assert any("D2" in w for w in brief.warnings)
    assert brief.single_message == "files GST in 60 seconds and saves 4 hours"   # not rewritten


# ================================================================================
# v3 §4 — hook_rack. "Does a human talk like this, and does it physically fit?"
# ================================================================================


def _line(slot="hook", text="files GST in sixty seconds flat", emotion="dry, certain",
          t_in=0.0, t_out=3.0, **over) -> dict:
    base = {"slot": slot, "t_in": t_in, "t_out": t_out, "text": text, "emotion": emotion}
    base.update(over)
    return base


def _rack(**over) -> dict:
    base = {
        "language": "en-IN",
        "body": [_line(slot="beat_01", t_in=3.0, t_out=7.0,
                       text="you open the app and the filing is already done",
                       emotion="matter of fact")],
        "hooks": [_line(slot="hook")],
        "selected_hook_slot": "hook",
    }
    base.update(over)
    return base


def test_the_wps_lint_is_recomputed_server_side_and_blocks_a_rushed_line():
    """W1 — pure arithmetic and the highest-ROI check in the product. A model
    grading its own line has every incentive to pass it, so the server recomputes
    exactly like it does for CCS."""
    from app.schemas import HookRack
    from app.validators import validate_hook_rack, wps_limits

    # the model claims it passes; the arithmetic says otherwise
    stuffed = HookRack.model_validate(_rack(hooks=[_line(
        text="this is a very long opening line that simply cannot be spoken inside the "
             "window it has been given no matter who reads it",
        t_in=0.0, t_out=3.0, words=3, wps=1.0, wps_verdict="pass")]))
    with pytest.raises(AgentValidationError) as exc:
        validate_hook_rack(stuffed)
    assert "lip-sync" in str(exc.value) and "proposed_fix" in str(exc.value)

    # a `fail` line WITH a proposed fix is allowed through so the user can apply it
    fixed = HookRack.model_validate(_rack(hooks=[_line(
        text="this is a very long opening line that simply cannot be spoken inside the "
             "window it has been given no matter who reads it",
        t_in=0.0, t_out=3.0, proposed_fix="filing, already done")]))
    ok = validate_hook_rack(fixed)
    assert ok.hooks[0].wps_verdict == "fail" and ok.hooks[0].proposed_fix

    # …and a comfortable line is graded pass, with the numbers the SERVER computed
    calm = validate_hook_rack(HookRack.model_validate(_rack()))
    assert calm.hooks[0].wps_verdict == "pass"
    assert calm.hooks[0].words == 6 and calm.hooks[0].wps <= wps_limits("en-IN")[0]


def test_a_line_with_no_real_emotion_is_rejected():
    """W2 — without an explicit read the model carries the PREVIOUS line's
    delivery into this one regardless of what it says."""
    from app.schemas import HookRack
    from app.validators import validate_hook_rack

    for bad in ("", "normal", "  Good  ", "neutral"):
        with pytest.raises(AgentValidationError) as exc:
            validate_hook_rack(HookRack.model_validate(_rack(hooks=[_line(emotion=bad)])))
        assert "carries the previous line's delivery" in str(exc.value)


def test_a_non_english_script_must_declare_its_loanwords():
    """W3 — forced translation is the tell that a local-language ad was machine
    made."""
    from app.schemas import HookRack
    from app.validators import validate_hook_rack

    with pytest.raises(AgentValidationError) as exc:
        validate_hook_rack(HookRack.model_validate(_rack(language="hi-IN")))
    assert "machine-made" in str(exc.value)

    ok = validate_hook_rack(HookRack.model_validate(
        _rack(language="hi-IN", loanwords_kept=["app", "GST"])))
    assert ok.loanwords_kept == ["app", "GST"]


def test_producing_hook_variants_leaves_the_body_byte_identical():
    """The whole economics of variant testing: a hook swap re-renders ONE shot,
    not the film. If the body drifts, every variant is a full re-render."""
    from app.schemas import HookRack
    from app.validators import validate_hook_rack

    first = validate_hook_rack(HookRack.model_validate(_rack()))

    more_hooks = HookRack.model_validate(_rack(
        hooks=[_line(slot="hook"), _line(slot="hook_b", text="sixty seconds, then it is filed")],
        selected_hook_slot="hook_b"))
    assert validate_hook_rack(more_hooks, previous=first) is more_hooks   # body untouched

    drifted = HookRack.model_validate(_rack(
        body=[_line(slot="beat_01", t_in=3.0, t_out=7.0,
                    text="the filing is done before you open the app",   # reworded
                    emotion="matter of fact")],
        hooks=[_line(slot="hook_b", text="sixty seconds, then it is filed")],
        selected_hook_slot="hook_b"))
    with pytest.raises(AgentValidationError) as exc:
        validate_hook_rack(drifted, previous=first)
    assert "body is LOCKED" in str(exc.value)


def test_the_wps_thresholds_reach_the_prompt_from_the_same_constant():
    """The R2 lexicon bug was one rule with two representations and nothing
    keeping them in sync. The threshold table is GENERATED from WPS_LIMITS, so a
    change that misses the prompt fails here rather than in a paid run."""
    from app.validators import WPS_LIMITS, script_thresholds_text

    text = script_thresholds_text()
    for lang, (tight, fail) in WPS_LIMITS.items():
        assert f"{lang}: tight above {tight} w/s, FAIL above {fail} w/s" in text
        # the ARITHMETIC travels too, not just the threshold. QA watched the
        # model write 10-word hooks into 3-second windows repeatedly: it had the
        # ceiling and still could not see its own line broke it, because
        # "3.3 w/s" and "how long must a 10-word line be" are different facts.
        assert f"a 10-word line needs at least {10 / fail:.1f}s" in text
        assert f"3.0s window holds at most {int(3.0 * fail)} words" in text
    assert "any other language" in text


# ================================================================================
# v3 §5 — the shot board. The LAST FREE GATE, and the artifact everything after
# it derives from.
# ================================================================================


def _shot(slot="shot_01", **over) -> dict:
    base = {
        "slot": slot, "duration_s": 3.0, "beat": "she lifts the bottle to the light",
        "action": "slow lift", "camera": "push_in", "shot_size": "MCU",
        "emotion": "quiet interest", "keyframe_prompt": "copper bottle, warm window light",
        "motion_prompt": "gentle push in", "model_route": "image_final",
        "route_reason": "legible on-pack text", "est_cost_usd": 0.08,
    }
    base.update(over)
    return base


def _board(**over) -> dict:
    base = {"creative_type": "image", "shots": [_shot()],
            "copy_primary": "no seam, no leak", "cta": "Shop the range"}
    base.update(over)
    return base


def test_b1_splits_a_two_action_beat_and_records_the_split():
    """A clip carrying two unrelated actions degrades reliably. This one is
    auto-fixable, so it is fixed AND recorded — the board logs its own edits
    instead of quietly applying them."""
    from app.schemas import ShotBoard
    from app.validators import validate_shot_board

    board = validate_shot_board(ShotBoard.model_validate(_board(shots=[
        _shot(beat="macro insert on the seam then back to her face")])))
    assert board.lints.beats.status == "warn"
    assert board.lints.beats.resolution and "split" in board.lints.beats.resolution[0]
    assert any("B1 auto-split" in c for c in board.changes)


def test_b2_flags_a_cast_the_runtime_cannot_carry():
    """Cast size is an OUTPUT of duration, not an input to it. This is the check
    that prevents a twenty-second film with four protagonists."""
    from app.schemas import ShotBoard
    from app.validators import validate_shot_board

    with pytest.raises(AgentValidationError) as exc:
        validate_shot_board(ShotBoard.model_validate(_board(shots=[
            _shot(slot="shot_01", cast_refs=["@priya"]),
            _shot(slot="shot_02", cast_refs=["@arjun", "@meera", "@sam"])])))
    assert "screen time" in str(exc.value)


def test_b3_forces_a_choice_and_never_silently_truncates():
    """A silently dropped product reference is exactly how label and geometry
    drift enter a campaign. The cap is per-model and lives in config, not in a
    prompt."""
    from app.schemas import ShotBoard
    from app.validators import validate_shot_board

    cap = config.MEDIA_REF_SLOTS["video"]
    over = [f"@ref{i}" for i in range(cap + 2)]
    with pytest.raises(AgentValidationError) as exc:
        validate_shot_board(ShotBoard.model_validate(_board(
            creative_type="video",
            shots=[_shot(model_route="video", product_refs=over,
                         route_reason="dialogue to camera")])))
    message = str(exc.value)
    assert "drop a reference or split the shot" in message
    assert "geometry drift" in message
    # the refs are all still named — nothing was quietly removed
    for ref in over:
        assert ref in message

    fits = validate_shot_board(ShotBoard.model_validate(_board(
        creative_type="video",
        shots=[_shot(model_route="video", product_refs=over[:cap],
                     route_reason="dialogue to camera")])))
    assert fits.lints.slots.status == "pass" and fits.shots[0].slots_used == cap


def test_b4_flags_a_multi_stage_camera_move():
    from app.schemas import ShotBoard
    from app.validators import validate_shot_board

    board = validate_shot_board(ShotBoard.model_validate(_board(shots=[
        _shot(motion_prompt="descend then orbit into a push")])))
    assert board.lints.motion.status == "warn"
    assert "one short clip holds one move" in board.lints.motion.findings[0]


def test_the_board_never_invents_a_model_id_or_a_price():
    """Model ids come from config.MEDIA_MODELS and costs from MEDIA_COST_USD.
    route_reason is user-visible and is a large part of why an agency trusts it."""
    from app.schemas import ShotBoard
    from app.validators import validate_shot_board

    with pytest.raises(AgentValidationError) as exc:
        validate_shot_board(ShotBoard.model_validate(_board(shots=[
            _shot(model_route="veo-3-ultra-turbo")])))
    assert "never the model's to invent" in str(exc.value)

    with pytest.raises(AgentValidationError) as exc:
        validate_shot_board(ShotBoard.model_validate(_board(shots=[_shot(route_reason="  ")])))
    assert "HARDEST requirement" in str(exc.value)

    priced = validate_shot_board(ShotBoard.model_validate(_board(shots=[
        _shot(slot="shot_01", est_cost_usd=0.08), _shot(slot="shot_02", est_cost_usd=0.15)])))
    assert priced.est_total_usd == 0.23


def test_the_board_shows_its_lints_even_when_everything_passes():
    """A lint panel that only appears on failure teaches the user nothing about
    what was checked."""
    from app.schemas import ShotBoard
    from app.validators import validate_shot_board

    board = validate_shot_board(ShotBoard.model_validate(_board()))
    for name in ("beats", "runtime", "slots", "motion"):
        assert getattr(board.lints, name).status == "pass"


# ================================================================================
# v3 §3 and §6 — style_block and canon_sheet
# ================================================================================


def test_skip_on_templates_yields_a_neutral_block_never_an_absent_one():
    """Every campaign HAS a style block; only its source varies. A downstream
    prompt that has to ask 'is there a style?' has two code paths and the second
    one is always the untested one."""
    from app.schemas import neutral_style_block

    block = neutral_style_block()
    assert block.derived_from is None            # Skip was chosen
    assert block.as_prompt()                      # …but there is still a string to inject
    for field in ("grade:", "light:", "lens:", "texture:", "motion:"):
        assert field in block.as_prompt()


def test_the_realism_dial_maps_to_texture_the_user_never_has_to_type():
    """Counter-intuitive and load-bearing: faces read as credible BECAUSE of
    pores and asymmetry. Prompts asking for flawless skin produce the synthetic
    look people recognise instantly."""
    from app.schemas import REALISM_TEXTURE, StyleBlock

    doc = StyleBlock(id="sb_doc", grade="g", light="l", lens="x",
                     texture=REALISM_TEXTURE["documentary"], motion="m", realism="documentary")
    assert "no beauty smoothing" in doc.as_prompt()
    assert "pores" in REALISM_TEXTURE["documentary"]
    assert set(REALISM_TEXTURE) == {"editorial", "natural", "documentary"}


def test_the_style_block_is_injected_verbatim_and_is_visible():
    """An agency tool shows the literal text that will reach the model."""
    from app.schemas import StyleBlock

    block = StyleBlock(id="sb1", grade="warm amber", light="low window key",
                       lens="85mm shallow", texture="fine grain", motion="locked off",
                       negatives=["watermark", "text overlay"])
    rendered = block.as_prompt()
    assert "warm amber" in rendered and "avoid: watermark, text overlay" in rendered


def test_a_canon_sheet_knows_what_complete_means_per_kind():
    from app.schemas import CANON_COVERAGE, CanonSheet

    char = CanonSheet(id="@priya", kind="character", label="Priya", brief="presenter",
                      coverage={v: True for v in CANON_COVERAGE["character"]})
    assert char.complete and len(char.required_views()) == 7

    thin = CanonSheet(id="@priya2", kind="character", label="P", brief="b",
                      coverage={"front": True})
    assert not thin.complete
    with pytest.raises(AgentValidationError) as exc:
        validators_mod.validate_canon_sheet(thin)
    assert "coverage incomplete" in str(exc.value)


def test_a_product_whose_geometry_mutates_needs_extra_three_quarter_views():
    """Product accuracy is harder than face consistency and packaging text is the
    hardest part of it. risk_notes names the features that mutate."""
    from app.schemas import CANON_COVERAGE, CanonSheet

    plain = CanonSheet(id="@bottle", kind="product", label="Tamra", brief="copper bottle",
                       coverage={v: True for v in CANON_COVERAGE["product"]})
    assert plain.complete
    assert validators_mod.validate_canon_sheet(plain) is plain

    risky = CanonSheet(id="@bottle2", kind="product", label="Tamra", brief="copper bottle",
                       risk_notes=["threaded lid", "engraved mark"],
                       coverage={v: True for v in CANON_COVERAGE["product"]})
    assert not risky.complete           # the same coverage is no longer enough
    assert len(risky.required_views()) > len(CANON_COVERAGE["product"])


def test_a_real_likeness_without_consent_cannot_be_used():
    from app.schemas import CANON_COVERAGE, CanonSheet

    sheet = CanonSheet(id="@real", kind="character", label="a real person",
                       brief="uploaded likeness", asset_ids=["up_1"], rights="unverified",
                       coverage={v: True for v in CANON_COVERAGE["character"]})
    with pytest.raises(AgentValidationError) as exc:
        validators_mod.validate_canon_sheet(sheet)
    assert "Record consent" in str(exc.value)

    sheet.rights = "consented"
    assert validators_mod.validate_canon_sheet(sheet) is sheet


def test_a_non_english_voice_needs_a_native_speaker_sign_off():
    """Blocking, per locale. Synthetic fluency is not evidence of correctness and
    a non-speaker cannot catch the failure."""
    from app.schemas import CanonSheet

    voice = CanonSheet(id="@vo", kind="voice", label="warm female", brief="pitch/pace")
    assert validators_mod.validate_canon_sheet(voice, languages=["en-IN"]) is voice

    with pytest.raises(AgentValidationError) as exc:
        validators_mod.validate_canon_sheet(voice, languages=["en-IN", "hi-IN"])
    assert "native-speaker sign-off for hi-IN" in str(exc.value)

    voice.native_review = {"hi-IN": "Rhea K"}
    assert validators_mod.validate_canon_sheet(voice, languages=["en-IN", "hi-IN"]) is voice


# ================================================================================
# v3 §7 §9 §11 — the keyframe gate, QC, and the variant reuse map
# ================================================================================


def _kf(slot="shot_01", approved=True, **over) -> dict:
    base = {"shot_slot": slot, "asset_id": f"as_{slot}", "approved": approved,
            "checks": {c: "pass" for c in ("face", "hands", "product_geometry",
                                           "label_legibility", "composition", "safe_area")},
            "cost_usd": 0.08}
    base.update(over)
    return base


def test_no_video_is_generated_until_every_keyframe_is_approved():
    """THE HARD GATE, and the highest-value addition in the spec. The pipeline
    went detail -> generate, so a composition or label defect surfaced only after
    video was paid for — where it is far harder to see in motion than in a
    still."""
    from app.schemas import KeyframeBoard
    from app.validators import gate_video_generation

    with pytest.raises(AgentValidationError) as exc:
        gate_video_generation(None, "video")
    assert "gated on approved stills" in str(exc.value)

    part = KeyframeBoard.model_validate({"frames": [_kf("shot_01"), _kf("shot_02", approved=False)]})
    assert part.all_approved is False
    with pytest.raises(AgentValidationError) as exc:
        gate_video_generation(part, "video")
    assert "shot_02" in str(exc.value)

    done = KeyframeBoard.model_validate({"frames": [_kf("shot_01"), _kf("shot_02")]})
    assert done.all_approved is True
    assert gate_video_generation(done, "video") is None

    # for an image campaign the keyframes ARE the deliverable — nothing to gate
    assert gate_video_generation(None, "image") is None


def test_all_approved_is_derived_not_asserted():
    """It is the flag that unlocks paid video generation, so it may not be
    something a model can simply set to true."""
    from app.schemas import KeyframeBoard

    lying = KeyframeBoard.model_validate(
        {"frames": [_kf("shot_01", approved=False)], "all_approved": True})
    assert lying.all_approved is False


def test_a_failing_check_needs_a_repair_or_an_explicit_acceptance():
    from app.schemas import KeyframeBoard
    from app.validators import validate_keyframe_board

    board = KeyframeBoard.model_validate({"frames": [
        _kf("shot_01", checks={"face": "pass", "label_legibility": "fail"})]})
    with pytest.raises(AgentValidationError) as exc:
        validate_keyframe_board(board)
    assert "Repair the region" in str(exc.value)

    repaired = KeyframeBoard.model_validate({"frames": [
        _kf("shot_01", checks={"face": "pass", "label_legibility": "fail"},
            repairs=["relit the label, rest of frame unchanged"])]})
    assert validate_keyframe_board(repaired) is repaired

    # and every shot on the board needs a frame at all
    with pytest.raises(AgentValidationError) as exc:
        validate_keyframe_board(KeyframeBoard.model_validate({"frames": [_kf("shot_01")]}),
                                board_slots=["shot_01", "shot_02"])
    assert "shot_02" in str(exc.value)


def test_an_enhancer_may_not_rewrite_identity_or_product_constraints():
    """Enhancers silently rewriting canon references and claims is a real and
    common failure — it undoes every gate before it."""
    from app.validators import validate_enhancement

    before = {"keyframe_prompt": "a", "cast_refs": ["@priya"], "claims_used": ["c"],
              "style_block_id": "sb1"}
    assert validate_enhancement(before, {**before, "keyframe_prompt": "a, richer"}) == [
        "keyframe_prompt"]

    with pytest.raises(AgentValidationError) as exc:
        validate_enhancement(before, {**before, "cast_refs": ["@someone_else"]})
    assert "silently undoes every gate" in str(exc.value)


def test_qc_holds_delivery_on_anything_blocking_and_derives_its_verdict():
    from app.schemas import QCReport
    from app.validators import build_qc_report

    from app.schemas import RightsEntry

    cid, _ = _filled("QC")
    context = campaign._context_of(cid)
    context.brand.rights_ledger = [
        RightsEntry(asset_kind="music", ref="bed.mp3", status="not_cleared")]

    report = build_qc_report(findings=[], automated={"identity_drift": "pass"},
                             rights_ledger=context.brand.rights_ledger,
                             final_copy="files GST in 60 seconds", context=context)
    assert report.verdict == "held"
    assert any(f.check == "rights" and f.tier == "blocking" for f in report.findings)

    # a model cannot talk its way to `cleared`
    lying = QCReport.model_validate({"findings": [
        {"tier": "blocking", "check": "rights", "detail": "uncleared"}], "verdict": "cleared"})
    assert lying.verdict == "held"


def test_a_banned_word_in_the_final_copy_blocks_even_though_it_passed_earlier():
    """Re-checked at the END on purpose: copy drifts during production, so the
    compliance answer from the brief stage is not the answer at delivery."""
    from app.validators import build_qc_report

    cid, _ = _filled("QC drift")
    context = campaign._context_of(cid)
    report = build_qc_report(findings=[], automated={}, rights_ledger=[],
                             final_copy="guaranteed to file on time", context=context)
    assert report.verdict == "held"
    assert any(f.check == "banned_word" for f in report.findings)

    # …and clean final copy clears
    clean = build_qc_report(findings=[], automated={}, rights_ledger=[],
                            final_copy="files GST in 60 seconds", context=context)
    assert clean.verdict == "cleared"


def test_an_accepted_defect_without_a_rationale_is_invalid():
    """'Ship it anyway' has to be an auditable decision rather than a shrug."""
    from app.schemas import QCFinding

    with pytest.raises(ValidationError) as exc:
        QCFinding(tier="accepted", check="continuity", detail="background shifts")
    assert "auditable decision" in str(exc.value)

    ok = QCFinding(tier="accepted", check="continuity", detail="background shifts",
                   rationale="off-brand-critical, dated campaign, client informed")
    assert ok.rationale


def test_an_unwired_detector_reports_skip_and_never_pass():
    """`skip` is honest when a detector is not wired yet; rendering it as pass
    would be a lie the whole report inherits."""
    from app.schemas import QCReport

    report = QCReport(automated={"lip_sync": "skip", "loudness": "pass"})
    assert report.automated["lip_sync"] == "skip"
    with pytest.raises(ValidationError):
        QCReport(automated={"lip_sync": "not_run"})


def test_a_retry_changes_exactly_one_variable_and_the_cause_picks_the_fix():
    """'Make it better' is not a repair instruction — each cause has a distinct
    fix, and two simultaneous edits make the next result uninterpretable."""
    from app.schemas import TAKE_FIXES, TakeReject

    reject = TakeReject(cause="too_many_actions", detail="two actions in one clip",
                        variable_changed="beat")
    assert reject.proposed_fix == TAKE_FIXES["too_many_actions"]
    assert "B1" in reject.proposed_fix

    with pytest.raises(ValidationError) as exc:
        TakeReject(cause="wrong_reference", detail="d", variable_changed="  ")
    assert "uninterpretable" in str(exc.value)


def test_the_variant_matrix_shows_the_reuse_that_makes_testing_affordable():
    """A hook-axis variant re-renders exactly the hook shot. That ratio IS the
    business case for the shot board, so it has to be visible."""
    from app.schemas import LOCALISATION_LIMITS, VariantMatrix

    matrix = VariantMatrix.model_validate({
        "cells": [{"variant_id": "B", "axis": "hook", "delta": "curiosity opener",
                   "hypothesis": "B tests the hook against A",
                   "shots_rerendered": ["shot_01"], "shots_reused": 4, "cost_usd": 0.08}],
        "baseline_cost_usd": 0.40, "matrix_cost_usd": 0.08})
    cell = matrix.cells[0]
    assert cell.shots_rerendered == ["shot_01"] and cell.shots_reused == 4
    assert matrix.matrix_cost_usd < matrix.baseline_cost_usd

    # the three tiers are different products, each with its honest limitation
    assert set(LOCALISATION_LIMITS) == {"dub", "revoice", "recast"}
    assert "lip-sync drift" in LOCALISATION_LIMITS["dub"]
    assert "full board re-render" in LOCALISATION_LIMITS["recast"]


# ================================================================================
# Stage checkpoints — a failure must not discard work that already succeeded
# ================================================================================


def test_a_completed_stage_is_never_paid_for_twice():
    """The reliability gap the owner prioritised over stitching. Observed live:
    the planner and three reviews all succeeded, then the last node's connection
    dropped and all four were discarded because intermediate results lived only
    in memory."""
    from app.graph import RuminationDeps, RuminationState, build_rumination_graph
    from app.schemas import Cadence, CampaignOptions, SeriesLevel

    saved: dict[str, dict] = {}
    calls = {"plan": 0, "review": 0}
    options = CampaignOptions(options=[
        CampaignOption(option_id="o1", name_line="n", description="d", storyline="s",
                       objective_echo="conversions", why_it_fits="w"),
        CampaignOption(option_id="o2", name_line="n2", description="d", storyline="s",
                       objective_echo="conversions", why_it_fits="w")])

    def run_options(*a, **k):
        calls["plan"] += 1
        return options

    def run_council(*a, **k):
        calls["review"] += 1
        raise RuntimeError("connection dropped mid-stream")

    def deps() -> RuminationDeps:
        return RuminationDeps(
            run_options=run_options, run_council=run_council,
            build_plan=lambda ctx, opt, sh: Plan(
                series=SeriesLevel(objective="conversions", north_star_metric="conversions",
                                   cadence=Cadence(type="one_time", concept_count=2)),
                concepts=[]),
            flagged_ids=lambda *a, **k: set(), merge_feedback=lambda a, b: a,
            objective_family=lambda o: o, seat_runner=lambda **k: (None, set()),
            load_checkpoint=saved.get,
            save_checkpoint=lambda stage, data: saved.__setitem__(stage, data))

    cid, tid = _filled("Checkpoint")
    context = campaign._context_of(cid)
    shadow = campaign._shadow_context(context)
    state = RuminationState(context=context, shadow=shadow)

    # first attempt: the planner succeeds, the review dies
    with pytest.raises(RuntimeError):
        build_rumination_graph(deps()).invoke(state)
    assert calls == {"plan": 1, "review": 1}
    assert "plan_options" in saved, "the successful planner was not checkpointed"

    # retry: the planner is SERVED FROM ITS CHECKPOINT, not re-run
    with pytest.raises(RuntimeError):
        build_rumination_graph(deps()).invoke(state)
    assert calls["plan"] == 1, "the planner was paid for twice"
    assert calls["review"] == 2, "the failed node did not re-run"

    # and what came back is the same object, not a husk
    restored = campaign.CampaignOptions.model_validate(saved["plan_options"]["options"])
    assert [o.option_id for o in restored.options] == ["o1", "o2"]


def test_only_a_complete_result_is_ever_restored():
    """The save happens AFTER the node returns, so a node that raised leaves no
    checkpoint — a half-finished stage can never be resumed into."""
    from app.graph import RuminationDeps, RuminationState, build_rumination_graph

    saved: dict[str, dict] = {}
    cid, _ = _filled("Half stage")
    context = campaign._context_of(cid)

    graph = build_rumination_graph(RuminationDeps(
        run_options=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("died mid-plan")),
        run_council=lambda *a, **k: (None, []), build_plan=lambda *a, **k: None,
        flagged_ids=lambda *a, **k: set(), merge_feedback=lambda a, b: a,
        objective_family=lambda o: o, seat_runner=lambda **k: (None, set()),
        load_checkpoint=saved.get,
        save_checkpoint=lambda stage, data: saved.__setitem__(stage, data)))

    with pytest.raises(RuntimeError):
        graph.invoke(RuminationState(context=context, shadow=campaign._shadow_context(context)))
    assert saved == {}, "a stage that raised left a checkpoint behind"


def test_a_new_rumination_clears_stale_checkpoints():
    """A checkpoint from a previous run is stale by definition — resuming into
    one would serve the user the campaign they already rejected."""
    cid, tid = _ruminated("Stale")
    store.save_checkpoint(tid, "plan_options", {"options": {"options": []}})
    assert store.load_checkpoint(tid, "plan_options") is not None

    store.clear_checkpoints(tid)
    assert store.load_checkpoint(tid, "plan_options") is None

    # a fresh rumination clears them itself
    store.save_checkpoint(tid, "review", {"feedback": None})
    campaign._options_turn(cid, tid)
    assert store.load_checkpoint(tid, "review") is None


# ================================================================================
# v3 §3 pipeline — the five inserted gates, and what the settings do to them
# ================================================================================


def test_the_v3_stages_are_inserted_in_cost_ladder_order():
    """Not arbitrary: the BOARD names which canon the campaign needs, canon
    COMPOSES into keyframes, and keyframes GATE motion. detail keeps its stage id
    because its artifact is upgraded in place, not replaced."""
    stages = campaign.CAMPAIGN_STAGES
    for new in ("brief", "script", "canon", "keyframes", "qc"):
        assert new in stages, f"{new} is not a stage"

    def before(a, b):
        return stages.index(a) < stages.index(b)

    assert before("intake", "brief") and before("brief", "options")
    assert before("templates", "script") and before("script", "detail")
    assert before("detail", "canon"), "the board names the canon it needs"
    assert before("canon", "keyframes"), "canon composes into keyframes"
    assert before("keyframes", "generate"), "nothing animates before the stills are locked"
    assert before("creative", "qc") and stages[-1] == "done"


def test_skip_steps_over_the_work_but_auto_never_does():
    """The safety property of the whole settings model: `skip` removes the WORK,
    `auto` removes only the PAUSE. Downstream stages consume upstream artifacts
    and the audit trail has to stay complete however fast the user moves."""
    cid, _ = _filled("Advance")

    # default: nothing is skipped
    assert campaign.advance_from(cid, "templates") == "script"
    assert campaign.advance_from(cid, "detail") == "canon"

    # skip canon → the flow steps over it entirely
    store.update_campaign_settings(cid, {"review_policy": {"gates": {"canon": "skip"}}})
    assert campaign.skipped_stages(cid) == {"canon"}
    assert campaign.advance_from(cid, "detail") == "keyframes"

    # auto on canon → the stage is STILL VISITED; only the pause is gone
    store.update_campaign_settings(cid, {"review_policy": {"gates": {"canon": "auto"}}})
    assert campaign.skipped_stages(cid) == set()
    assert campaign.advance_from(cid, "detail") == "canon"
    assert campaign._pauses_at(cid, "canon") is False


def test_skipping_both_skippable_stages_still_reaches_the_gates_that_matter():
    cid, _ = _filled("Skip both")
    store.update_campaign_settings(cid, {"review_policy": {
        "gates": {"templates": "skip", "canon": "skip"}}})

    assert campaign.advance_from(cid, "options") == "script"    # stepped over templates
    assert campaign.advance_from(cid, "detail") == "keyframes"  # stepped over canon
    # …and the two gates that can never be skipped are still on the path
    assert campaign.advance_from(cid, "keyframes") == "generate"
    assert campaign.advance_from(cid, "creative") == "qc"


# ================================================================================
# v3 end-to-end — the pipeline as a user walks it
# ================================================================================


def test_the_brief_gate_comes_before_any_option_is_written():
    """Options written before the single message exists are options written
    against a moving target — so the brief is the first thing the flow does."""
    cid, tid = _filled("Brief first")
    campaign.begin_rumination(cid)

    assert store.get_thread(tid)["stage"] == "brief"
    assert _artifacts(tid, "campaign_brief"), "no brief was produced"
    assert _artifacts(tid, "campaign_option") == [], "options ran before the brief was approved"

    _approve(tid, "brief", "approve_brief")
    assert _artifacts(tid, "campaign_option"), "approving the brief did not start the options"


def test_the_picked_template_actually_reaches_every_visual_prompt(template_library):
    """The style block exists so a template CHANGES WHAT IS RENDERED. Before v3
    the descriptors were a loose list nothing downstream enforced."""
    cid, tid = _ruminated("Style travels")
    _act(tid, "o1", "approve")
    _act(tid, "templates", "pick_t1")

    card = _artifacts(tid, "campaign_detail")[-1]["payload"]
    block = card["style_block"]
    assert block["derived_from"] == "t1"

    from app.schemas import StyleBlock
    injected = StyleBlock.model_validate(block).as_prompt()
    for shot in card["board"]["shots"]:
        assert shot["keyframe_prompt"].startswith(injected), \
            "the style block was not prefixed onto this shot"


def test_a_video_campaign_walks_script_board_canon_keyframes_in_order():
    """The cost ladder in practice: three free gates, then the cheap stills, and
    only then anything that costs motion."""
    cid, tid = _ruminated("Video ladder", creative_type="video")
    _act(tid, "o1", "approve")
    _act(tid, "templates", "skip")

    assert _artifacts(tid, "hook_rack"), "no script for a video campaign"
    assert store.get_thread(tid)["stage"] == "script"
    _approve(tid, "hook_rack", "approve_script")

    assert _artifacts(tid, "campaign_detail"), "no shot board"
    assert store.get_thread(tid)["stage"] == "detail"
    _approve(tid, "board", "approve_board")

    assert _artifacts(tid, "canon_sheet"), "no canon sheets"
    _approve(tid, "canon", "approve_canon")

    frames = _artifacts(tid, "keyframe_board")
    assert frames, "no keyframes — the hard gate never ran"
    assert frames[-1]["payload"]["board"]["all_approved"] is False


def test_an_image_campaign_skips_the_script_it_has_no_use_for():
    """A still has no words-per-second problem. A gate that protects nothing is
    a gate that costs a turn."""
    cid, tid = _ruminated("Image no script")
    _act(tid, "o1", "approve")
    _act(tid, "templates", "skip")

    assert _artifacts(tid, "hook_rack") == []
    assert _artifacts(tid, "campaign_detail"), "the board should follow templates directly"


def test_skipping_canon_is_recorded_with_what_it_costs():
    """Practitioners genuinely skip sheets under time pressure. The failure is
    doing it invisibly, so the trade is stated and written to the audit trail."""
    cid, tid = _ruminated("Skip canon", creative_type="video")
    store.update_campaign_settings(cid, {"review_policy": {"gates": {"canon": "skip"}}})
    _act(tid, "o1", "approve")
    _act(tid, "templates", "skip")
    _approve(tid, "hook_rack", "approve_script")
    _approve(tid, "board", "approve_board")

    assert _artifacts(tid, "canon_sheet") == []
    rows = [a for a in store.get_artifact_activity(tid, "canon") if a["event"] == "skipped"]
    assert rows and "identity drift" in rows[0]["detail"]
    assert _artifacts(tid, "keyframe_board"), "skipping canon should go straight to keyframes"


def test_qc_stands_between_the_creative_and_the_ad_card():
    cid, tid = _ruminated("QC gate")
    _act(tid, "o1", "approve")
    _act(tid, "detail", "generate_creative")
    _act(tid, "confirm", "generate_single")
    _act(tid, "creative", "accept_all")

    assert store.get_thread(tid)["stage"] == "qc"
    assert store.list_ad_cards(cid) == [], "the Ad Card was assembled before QC cleared"
    report = _artifacts(tid, "qc_report")[-1]["payload"]["report"]
    assert report["verdict"] == "cleared"
    # unwired detectors are honest about it
    assert report["automated"]["lip_sync"] == "skip"

    _act(tid, "qc", "deliver")
    assert store.list_ad_cards(cid), "deliver did not assemble the Ad Card"


def test_the_reference_budget_reaches_the_prompt_from_the_same_constant():
    """Found by product QA: the board prompt said references were "budgeted"
    without ever saying the budget, so the model attached three to a route that
    carries two and could not recover. The check knew a number the prompt did
    not — the same shape as the R2 lexicon bug and the W1 table."""
    from app.validators import ref_slots_text

    text = ref_slots_text()
    for route, cap in config.MEDIA_REF_SLOTS.items():
        assert f"{route}: {cap} reference(s)" in text
    assert "anything else" in text


def test_the_board_is_handed_the_claims_it_is_allowed_to_use(monkeypatch):
    """Also found by QA: the model wrote claims_used=['proof_points:<slug>'] —
    an id it invented, because it was never handed the strings themselves."""
    seen: dict = {}
    real = campaign.run_agent

    def spy(**kwargs):
        if kwargs.get("agent") == "shot_board":
            seen.update(kwargs["user_payload"])
        return real(**kwargs)

    monkeypatch.setattr(campaign, "run_agent", spy)
    cid, tid = _ruminated("Board claims")
    _act(tid, "o1", "approve")
    _pass_script(tid)

    assert seen, "the board agent never ran"
    assert seen["approved_claims"] == sorted(BRAND["approved_claims"])


# ================================================================================
# v3 §8 — the classified take reject, driven the way the UI drives it
# ================================================================================


def _generated(name: str) -> tuple[str, str]:
    cid, tid = _ruminated(name)
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")
    _act(tid, "confirm", "generate_single")
    return cid, tid


def test_a_reject_carries_its_cause_and_proposes_the_matching_fix():
    """'Make it better' is not a repair instruction. Each of the five causes has
    a distinct patch, and the reject event carries which one applies."""
    from app.schemas import TAKE_FIXES

    cid, tid = _generated("Reject cause")
    slot = campaign._ws(tid)["items"][0]["slot"]

    _act(tid, "creative", f"reject_too_many_actions_{slot}")
    history = campaign._ws(tid)["rejects"][slot]
    assert history and history[-1]["cause"] == "too_many_actions"
    assert history[-1]["proposed_fix"] == TAKE_FIXES["too_many_actions"]

    rows = [a["detail"] for a in store.get_artifact_activity(tid, "creative")
            if a["event"] == "downgraded"]
    assert any("too_many_actions" in d for d in rows)
    # the fix is offered as an action the user can actually take
    card = _artifacts(tid, "creative_set")[-1]
    assert any(a["event"] == f"reroll_{slot}" for a in card["actions"])


def test_two_retries_cannot_change_the_same_variable():
    """Two simultaneous or repeated edits make the next result uninterpretable,
    so a retry that moves the same variable again is refused, not run."""
    cid, tid = _generated("One variable")
    slot = campaign._ws(tid)["items"][0]["slot"]

    _act(tid, "creative", f"reject_wrong_reference_{slot}")
    before = len(campaign._ws(tid)["rejects"][slot])

    _act(tid, "creative", f"reject_wrong_reference_{slot}")   # same variable again
    assert len(campaign._ws(tid)["rejects"][slot]) == before, "the repeat was recorded"
    assert "already did" in _envelopes(tid)[-1]["text"]

    # …but a DIFFERENT cause moves a different variable and is accepted
    _act(tid, "creative", f"reject_unsuitable_model_{slot}")
    assert len(campaign._ws(tid)["rejects"][slot]) == before + 1


def test_the_variant_matrix_shows_what_was_reused_not_re_rendered():
    """The ratio between deriving variants from the board and re-rendering them
    from scratch IS the business case for having a board, so it is shown rather
    than left to be inferred."""
    cid, tid = _ruminated("Variant matrix")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")
    _text(tid, "2 variants")
    _act(tid, "creative", "accept_all")
    _act(tid, "qc", "deliver")

    cards = _artifacts(tid, "variant_matrix")
    assert cards, "no variant matrix after a variant set was delivered"
    m = cards[-1]["payload"]["matrix"]
    assert len(m["cells"]) >= 2
    assert m["matrix_cost_usd"] < m["baseline_cost_usd"], "reuse is not visible"
    control = [c for c in m["cells"] if "control" in c["delta"]]
    assert control and control[0]["shots_rerendered"] == [], "the control re-rendered something"


def test_a_single_creative_gets_no_variant_matrix():
    """One control is not a matrix — saying so would be noise."""
    cid, tid = _ruminated("No matrix")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _act(tid, "detail", "generate_creative")
    _act(tid, "confirm", "generate_single")
    _act(tid, "creative", "accept_all")
    _act(tid, "qc", "deliver")
    assert _artifacts(tid, "variant_matrix") == []


def test_every_artifact_the_server_emits_has_a_renderer_registered():
    """Found in the browser, not by a test: campaign_brief was in the renderer's
    switch but NOT in CAMPAIGN_ARTIFACT_TYPES, the allowlist that decides which
    artifacts reach that renderer at all. It fell through to the legacy card and
    drew a placeholder with NO ACTION ROW — a dead end at a gate, in a flow that
    passed every API test.

    Two lists that must agree is the same drift shape as the R2 lexicon and the
    W1 table. This one crosses a language boundary, so the guard lives here and
    reads the TypeScript.
    """
    web = Path(__file__).resolve().parents[2] / "plotline-web"
    source = (web / "components" / "campaign-artifacts.tsx").read_text()

    # split on "= [" not "]": the declaration is `: ArtifactType[] = [`, and the
    # first "]" belongs to the TYPE, not to the list.
    block = source.split("export const CAMPAIGN_ARTIFACT_TYPES")[1].split("= [", 1)[1]
    block = block.split("\n];", 1)[0]
    listed = set(re.findall(r'"([a-z_]+)"', block))
    switched = set(re.findall(r'case "([a-z_]+)":', source))

    missing = switched - listed
    assert not missing, (
        f"{sorted(missing)} have a renderer but are not in CAMPAIGN_ARTIFACT_TYPES, "
        "so they will never reach it — they render as a placeholder with no actions")

    # …and every v3 type the SERVER can emit is actually handled
    from app.schemas import ArtifactType
    import typing
    v3 = {"campaign_brief", "hook_rack", "canon_sheet", "keyframe_board",
          "qc_report", "variant_matrix"}
    assert v3 <= set(typing.get_args(ArtifactType)), "the server lost an artifact type"
    assert v3 <= switched, f"no renderer for {sorted(v3 - switched)}"
    assert v3 <= listed, f"not routed to the campaign renderer: {sorted(v3 - listed)}"


def test_every_cta_a_card_declares_is_answerable_in_the_chat():
    """The artifact panel is a READ-ONLY review surface (owner decision
    2026-08-26): a card still DECLARES its actions, but the user answers in the
    composer. So any turn whose artifact offers actions must also carry a
    question that surfaces every one of them.

    This is the dead-end bug in its new form. Previously a gate rendered with
    no buttons because two lists disagreed; now the risk is a CTA that exists
    in the payload with nowhere to be pressed. Options are LIFTED from the
    actions rather than hand-written beside them, and this asserts the lift
    actually covers what was declared. Verified non-vacuous: it inspects 7
    actioned cards on this walk.
    """
    cid, tid = _ruminated("CTA reachability")
    _act(tid, "o1", "approve")
    _pass_script(tid)

    inspected, unreachable = 0, []
    for env in _envelopes(tid):
        question = env.get("question") or {}
        # (artifact_id, event) PAIRS, not events alone. Comparing bare event
        # names hid a real dead end: the options turn posts three cards that
        # each declare "approve", so the name set matched while only the LAST
        # card's approve was actually offered — options 1 and 2 could not be
        # approved at all. An identity has to include what it acts ON.
        offered = {
            (o.get("artifact_id"), o["event"])
            for o in question.get("options", []) if o.get("event")
        }
        for card in env.get("artifacts", []):
            declared = {
                (card["id"], a["event"]) for a in card.get("actions", []) if a.get("event")
            }
            if not declared:
                continue
            inspected += 1
            if not declared <= offered:
                unreachable.append(
                    (card["type"], card["id"], sorted(e for _, e in declared - offered)))

    assert inspected, "no actioned cards on this walk — the test would pass vacuously"
    assert not unreachable, (
        "these CTAs are declared on a card but cannot be answered in the chat, "
        f"so the user has no way to press them: {unreachable}")


def test_three_options_are_each_separately_approvable():
    """The options turn posts one card per option, and a user must be able to
    approve ANY of them. Found by driving the browser: the chat offered a
    single unlabelled 'Approve' that always meant option three, so options one
    and two were unreachable while every API test passed.

    Labels have to name the option too — three buttons all reading 'Approve'
    cannot tell you what you are approving.
    """
    cid, tid = _ruminated("Pick any option")
    turn = next(e for e in reversed(_envelopes(tid))
                if any(a["type"] == "campaign_option" for a in e.get("artifacts", [])))

    option_ids = [a["id"] for a in turn["artifacts"] if a["type"] == "campaign_option"]
    assert len(option_ids) >= 2, "need at least two options for this to prove anything"

    options = (turn.get("question") or {}).get("options", [])
    approvable = {o["artifact_id"] for o in options if o.get("event") == "approve"}
    assert set(option_ids) <= approvable, (
        f"only {sorted(approvable)} can be approved, but the turn offered {option_ids}")

    labels = [o["label"] for o in options if o.get("event") == "approve"]
    assert len(set(labels)) == len(labels), f"approve labels are ambiguous: {labels}"


def test_deleting_a_campaign_takes_its_thread_and_messages_with_it():
    """There was no delete route until 2026-08-26, which is how a dev database
    reached 31 QA campaigns with no way to clear them. Deletion has to be a
    real cascade: a row left behind is an orphan the grid cannot show and
    nothing can reach."""
    cid, tid = _ruminated("Delete me")
    assert store.get_messages(tid), "nothing to orphan — the test would prove nothing"

    removed = store.delete_series(cid)

    assert store.get_series(cid) is None
    assert store.get_thread(tid) is None
    assert store.get_messages(tid) == []
    assert removed["series"] == 1 and removed["threads"] >= 1
    assert removed.get("thread_messages", 0) >= 1, "messages were left behind"


def test_canon_sheets_survive_the_campaign_that_made_them():
    """Canon is workspace-global by owner decision (2026-08-25), so it outlives
    its campaign. A cascade that swept it would silently strip the shared
    library every time a user tidied up."""
    cid, _ = _filled("Canon owner")
    sheet_id = store.save_canon_sheet(
        {"id": "cs_keep", "kind": "cast", "name": "Runner", "views": [], "locks": []})
    store.delete_series(cid)
    assert store.get_canon_sheet(sheet_id or "cs_keep") is not None, (
        "deleting a campaign stripped the workspace canon library")


def _claims_question(tid):
    for envelope in reversed(_envelopes(tid)):
        question = envelope.get("question") or {}
        if any(o.get("event") == "confirm_claims" for o in question.get("options", [])):
            return question
    return None


def test_claims_are_confirmed_in_the_chat_now_that_the_brand_card_is_gone(monkeypatch):
    """claims_confirmed was reachable only through the Brand card's one-tap.
    The card is gone, so the confirmation had to move or the compliance chain
    would break silently — an unconfirmed list grants nothing, so every claim
    would become a kill flag with no way for the user to say otherwise.

    The agent still may not confirm on the user's behalf (_validate_intake),
    which is exactly why this has to be a question and not an inference.
    """
    _scripted_intake(monkeypatch, {"product": PRODUCT, "campaign": CAMPAIGN,
                                   "brand": {**BRAND, "claims_confirmed": False}})
    started = campaign.start_campaign("Claims in chat")
    cid, tid = started["campaign_id"], started["thread"]["id"]
    for turn in ("the product is Ledger", "campaign objective is conversions", "brand details next"):
        _text(tid, turn)

    question = _claims_question(tid)
    assert question, "no claims confirmation was ever offered"
    assert question["multi"] is True
    offered = [o["label"] for o in question["options"] if not o.get("event")]
    assert set(offered) == set(BRAND["approved_claims"])
    assert "kill flag" in (question["note"] or ""), "the consequence must be stated inline"

    # confirm ONE of the two — the other must become unusable, not quietly kept
    keep = BRAND["approved_claims"][0]
    campaign.handle_event(UserEvent(thread_id=tid, type="action", action={
        "artifact_id": "intake", "event": "confirm_claims", "values": [keep]}))

    brand = store.get_series(cid)["context"]["brand"]
    assert brand["claims_confirmed"] is True
    assert brand["approved_claims"] == [keep]


def test_confirming_zero_claims_is_still_a_confirmation(monkeypatch):
    """The 2026-08-25 lesson, re-checked at its new home: an optional input
    wired to a mandatory output is unreachable. If 'none of them' did not
    confirm, a user with nothing quotable could never leave the gate."""
    _scripted_intake(monkeypatch, {"product": PRODUCT, "campaign": CAMPAIGN,
                                   "brand": {**BRAND, "claims_confirmed": False}})
    started = campaign.start_campaign("Zero claims")
    cid, tid = started["campaign_id"], started["thread"]["id"]
    for turn in ("the product is Ledger", "campaign objective is conversions", "brand details next"):
        _text(tid, turn)

    campaign.handle_event(UserEvent(thread_id=tid, type="action", action={
        "artifact_id": "intake", "event": "confirm_claims_none"}))

    brand = store.get_series(cid)["context"]["brand"]
    assert brand["claims_confirmed"] is True, "confirming an empty list must still confirm"
    assert brand["approved_claims"] == []
    assert campaign._confirmed_claims(CampaignContext.model_validate(
        store.get_series(cid)["context"])) == set()


def test_a_restart_mid_campaign_does_not_lose_the_brief():
    """Found by driving the browser, not by the suite: restarting the API
    between approving the brief and reaching the board handed shot_board an
    EMPTY brief and a null option. The agent refused honestly with 'INCOMPLETE
    INPUT' and the run escalated — so the visible failure was three wasted
    attempts at the board, and the real cause was upstream.

    _rehydrate was written for the v2 artifacts and never taught the five v3
    ones. Same species as the renderer allowlist and the R2 lexicon: a list
    grew and the thing that reads it did not. This asserts every v3 artifact
    the workspace depends on survives losing the process.
    """
    cid, tid = _ruminated("Restart safe")
    _act(tid, "o1", "approve")
    _pass_script(tid)

    before = campaign._ws(tid)
    assert before.get("brief"), "no brief on the workspace — the test would prove nothing"
    keys = [k for k in ("brief", "hook_rack") if before.get(k)]

    # the process dies: every in-memory workspace goes with it
    campaign._WORKSPACES.clear()

    after = campaign._ws(tid)
    campaign._rehydrate(tid, after)

    for key in keys:
        assert after.get(key), f"{key} did not survive a restart — the stage after it gets nothing"
    assert after["brief"] == before["brief"]
    assert after.get("approved_option") is not None, "the approved option did not survive either"


# ---- PixelBin wire contract -------------------------------------------------
# Four separate bugs shipped in the first cut of this client, and every one of
# them only surfaced at GENERATION time — i.e. after the user had approved a
# cost gate. Listing models and reading schemas worked throughout, which is
# exactly what made them easy to miss. These pin the wire shape.


def test_pixelbin_form_fields_are_namespaced_under_input():
    """The endpoint validates against /input, so a flat `prompt` field comes
    back as 'missingProperty: prompt' while the value is sitting in the body."""
    from app import pixelbin_client as pb

    fields = dict(pb._as_form({"prompt": "x", "aspect_ratio": "9:16"}))
    assert "input.prompt" in fields and "prompt" not in fields
    assert fields["input.aspect_ratio"] == "9:16"

    # a list repeats under one field name; a JSON array is silently ignored
    repeated = pb._as_form({"images": ["a", "b"]})
    assert repeated == [("input.images", "a"), ("input.images", "b")]


def test_pixelbin_platform_auth_is_base64_not_the_raw_token(monkeypatch):
    """/service/public/* accepts the raw token and /service/platform/* does not.
    So schemas and model lists work while generation 401s — the one call that
    costs money is the only one that fails."""
    import base64
    from app import pixelbin_client as pb

    monkeypatch.setattr(config, "PIXELBIN_API_TOKEN", "tok-123")
    header = pb._headers()["Authorization"]
    assert header == "Bearer " + base64.b64encode(b"tok-123").decode()
    assert "tok-123" not in header, "the raw token must not travel"


def test_every_configured_pixelbin_model_has_a_capability_row():
    """The capability table says which parameters a model actually declares —
    video takes image_urls where image takes images, duration is a STRING enum,
    and nanoBanana has no output_resolution at all. A configured model with no
    row means the client would guess, and a guessed parameter is a 400 that
    reads like an outage. Two lists that must agree, so something compares them.
    """
    from app import pixelbin_client as pb

    missing = [m for m in config.PIXELBIN_MODELS.values() if m not in pb._CAPS]
    assert not missing, f"no capability row for {missing} — add it from the schema endpoint"


def test_pixelbin_snaps_ratio_and_duration_into_the_model_enum():
    """Veo offers only 9:16 and 16:9 and durations 4/6/8. A brief outside those
    has to land somewhere DELIBERATELY, not as a provider error the user reads
    as an outage."""
    from app import pixelbin_client as pb

    veo = pb._CAPS["veo31_generate"]
    assert pb._snap_ratio("9:16", veo["ratios"]) == "9:16"
    assert pb._snap_ratio("16:9", veo["ratios"]) == "16:9"
    # by ratio distance: 1.0 is 0.44 from 0.5625 and 0.78 from 1.78, so a square
    # brief lands PORTRAIT. Deterministic and stated, rather than a coin flip.
    assert pb._snap_ratio("1:1", veo["ratios"]) == "9:16"
    assert pb._snap_ratio("4:5", veo["ratios"]) == "9:16"      # 0.8 → nearer 0.5625
    assert pb._snap_ratio("21:9", veo["ratios"]) == "16:9"     # 2.33 → nearer 1.78
    assert pb._snap_ratio("garbage", veo["ratios"]) == "9:16"  # unparseable never crashes

    assert pb._snap_duration(6, veo["durations"]) == "6"
    assert pb._snap_duration(5, veo["durations"]) in {"4", "6"}
    assert pb._snap_duration(30, veo["durations"]) == "8"      # clamps, never passes 30 through
    assert isinstance(pb._snap_duration(6, veo["durations"]), str), "the enum is strings, not ints"


def test_per_stage_models_default_to_the_slot_they_already_used(monkeypatch):
    """A new dial, not a new behaviour: every stage falls through to the broad
    slot it shared before, so an unset environment changes nothing. Six agents
    on one PLANNER_MODEL meant paying planner rates to lint a shot board."""
    import importlib
    monkeypatch.delenv("PLOTLINE_MODEL_BOARD", raising=False)
    monkeypatch.setenv("PLOTLINE_PLANNER_MODEL", "planner-x")
    monkeypatch.setenv("PLOTLINE_INTAKE_MODEL", "intake-x")
    monkeypatch.setenv("PLOTLINE_FEEDBACK_MODEL", "council-x")
    fresh = importlib.reload(config)
    try:
        assert fresh.STAGE_MODELS["board"] == "planner-x"
        assert fresh.STAGE_MODELS["options"] == "planner-x"
        assert fresh.STAGE_MODELS["intake"] == "intake-x"
        assert fresh.STAGE_MODELS["council"] == "council-x"

        # …and an explicit override wins for that stage ALONE
        monkeypatch.setenv("PLOTLINE_MODEL_BOARD", "cheap-board")
        fresh = importlib.reload(config)
        assert fresh.STAGE_MODELS["board"] == "cheap-board"
        assert fresh.STAGE_MODELS["options"] == "planner-x", "overriding one stage moved another"
    finally:
        importlib.reload(config)


def test_no_stage_model_leaks_into_the_public_api(monkeypatch):
    """The settings gear is honestly-inert and the whole API is swept for
    anything naming a model. Per-stage models are an OPERATOR knob in .env;
    exposing them as a request field would quietly make that sweep a lie."""
    spec = main.app.openapi()
    body_props = {p for _, p in _request_body_properties(spec)}
    assert not [p for p in body_props if "model" in p.lower() or "stage_model" in p.lower()]


def test_a_stage_refuses_to_run_without_the_state_it_depends_on():
    """`shot_board` was once handed {"brief": {}, "option": null}, refused with
    "INCOMPLETE INPUT", burned three attempts and escalated as "the shot board
    kept failing its lints" — blaming the last node for a fault two stages back.

    Note that TYPING the workspace would not catch this: {} is a valid dict.
    What catches it is declaring what a stage requires and refusing to spend a
    model call without it. The error must name the stage to go back to.
    """
    empty: dict = {}
    with pytest.raises(campaign.WorkspaceIncomplete) as exc:
        campaign._require(empty, "board")
    message = str(exc.value)
    assert "brief" in message and "the brief gate" in message
    assert "nothing was generated" in message, "the user must be told they were not charged"

    # a present-but-EMPTY slot is still missing — this is the actual bug shape
    with pytest.raises(campaign.WorkspaceIncomplete):
        campaign._require({"brief": {}, "approved_option": None}, "board")

    # …and a satisfied stage passes silently
    campaign._require({"brief": {"x": 1}, "approved_option": object()}, "board")


def test_every_required_slot_has_a_named_source():
    """The error's whole value is naming where to go back to. A slot with no
    source silently degrades to 'an earlier stage', which is the message the
    guard exists to replace."""
    declared = {slot for slots in campaign.STAGE_REQUIRES.values() for slot in slots}
    unnamed = declared - set(campaign._SLOT_SOURCE)
    assert not unnamed, f"these required slots have no named source: {sorted(unnamed)}"


def test_the_board_survives_a_restart_not_just_its_projection():
    """The board is the source of truth; `detail` is its projection. _rehydrate
    restored only the projection, so canon, keyframes and qc read ws["board"]
    as {} — which meant shots == [] and a keyframe stage that rendered NOTHING
    rather than failing. A silent no-op is worse than an error."""
    cid, tid = _ruminated("Board survives")
    _act(tid, "o1", "approve")
    _pass_script(tid)
    _approve(tid, "board", "approve_board")

    before = campaign._ws(tid)
    assert before.get("board", {}).get("shots"), "no board to lose — the test would prove nothing"

    campaign._WORKSPACES.clear()
    after = campaign._ws(tid)
    campaign._rehydrate(tid, after)

    assert after.get("board"), "the board did not survive; canon and keyframes would render nothing"
    assert after["board"]["shots"] == before["board"]["shots"]
    assert after.get("style_block"), "the style block rides the same payload and was dropped"


def test_a_paid_render_whose_download_fails_is_still_recorded(monkeypatch, tmp_path):
    """FMEA P0 (RPN 224). The provider generates and BILLS, then the file is
    downloaded. A CDN hiccup between those two steps used to lose everything:
    no asset, no generation_log row, no cost — money moved and nothing pointed
    at it. The spend has to survive the failure that loses the file.
    """
    from app import media, store

    monkeypatch.setattr(config, "MOCK_MEDIA", False)
    monkeypatch.setattr(media, "_provider_order", lambda kind: ["fal"])
    monkeypatch.setattr(media, "_generate_fal",
                        lambda *a, **k: ("https://cdn.example/x.png", "fal:test-model"))

    attempts = {"n": 0}
    def always_fails(url, dest):
        attempts["n"] += 1
        raise OSError("connection reset")
    monkeypatch.setattr(media, "_download", always_fails)

    before = len(store.recent_generations(500))
    with pytest.raises(media.MediaError) as exc:
        media.generate("image", "a shoe on wet rock", ratio="9:16")

    assert attempts["n"] == 2, "a 5MB file over a proxy deserves one retry before giving up"
    message = str(exc.value)
    assert "charged" in message and "https://cdn.example/x.png" in message, (
        "the user must be told they were charged, and where the render still is")

    rows = store.recent_generations(500)
    assert len(rows) == before + 1, "the spend was not recorded — the money is invisible"
    orphan = rows[0]
    assert orphan["event"] == "image_orphaned"
    assert orphan["cost"] > 0 and orphan["model"] == "fal:test-model"


def test_the_script_and_board_actually_receive_the_approved_option():
    """`approved_option_json` was READ by the script and board turns and WRITTEN
    by nothing. Both were handed option: null for the entire life of v3, and had
    to invent the campaign from the brief alone.

    It surfaced as a lint failure, not a missing input: asked for a two-shot
    film the board produced eight shots and six characters, burned three
    attempts and escalated as "the shot board kept failing its lints" — the
    model blamed for a fact nobody gave it. Approving an option has to mean the
    stages after it can SEE the option.
    """
    cid, tid = _ruminated("Option reaches the board", creative_type="video")
    _act(tid, "o1", "approve")

    ws = campaign._ws(tid)
    assert ws.get("approved_option") is not None, "nothing was approved — test proves nothing"

    payload = campaign._approved_option_json(ws)
    assert payload is not None, "the board would be handed option: null"
    assert payload.get("option_id") == "o1"
    # derived, never mirrored — a second slot is how the two diverged before
    assert "approved_option_json" not in ws, (
        "a mirrored copy is back; derive it instead so the two cannot diverge")
