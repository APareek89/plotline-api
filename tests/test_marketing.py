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
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import brand_extract, campaign, ccs as ccs_mod, config, main, store, threadkit
from app.agents import campaign_mock
from app.schemas import (
    AdCard,
    AgentMessage,
    CampaignContext,
    CampaignDetail,
    CampaignOption,
    CampaignOptions,
    Feedback,
    ModelConfirm,
    Plan,
    SeatReview,
    SeatScore,
    UserEvent,
    objective_family,
)
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


def _filled(campaign_id: str, creative_type: str = "image") -> tuple[str, str]:
    """Steps 0-2 via path a: named campaign + three saved cards."""
    started = campaign.start_campaign(campaign_id)
    cid, tid = started["campaign_id"], started["thread"]["id"]
    campaign.save_block(cid, "product", PRODUCT)
    campaign.save_block(cid, "campaign", {**CAMPAIGN, "creative_type": creative_type})
    campaign.save_block(cid, "brand", BRAND)
    return cid, tid


def _ruminated(name: str, creative_type: str = "image") -> tuple[str, str]:
    """…through step 3: options on the thread, awaiting an approval."""
    cid, tid = _filled(name, creative_type)
    campaign.begin_rumination(cid)
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


def test_path_b_fills_the_identical_campaign_context(monkeypatch):
    """Conversational intake is elicitation UX, not a second data model: the
    stored context is byte-identical to the one the three cards write."""
    _scripted_intake(monkeypatch, {"product": PRODUCT, "campaign": CAMPAIGN,
                                   "brand": {**BRAND, "claims_confirmed": False}})

    structured_id, _ = _filled("Path A")

    started = campaign.start_campaign("Path B")
    conversational_id, tid = started["campaign_id"], started["thread"]["id"]
    _text(tid, "help me define the campaign")
    assert campaign._ws(tid)["sub_mode"] == "conversational"
    for turn in ("the product is Ledger", "campaign objective is conversions", "brand details next"):
        _text(tid, turn)
    campaign.save_block(conversational_id, "brand", {"claims_confirmed": True})  # the one-tap confirm

    path_a = store.get_series(structured_id)["context"]
    path_b = store.get_series(conversational_id)["context"]
    assert CampaignContext.model_validate(path_a) and CampaignContext.model_validate(path_b)
    assert {k: v for k, v in path_a.items() if k != "name"} == \
           {k: v for k, v in path_b.items() if k != "name"}
    assert campaign.cards_done(path_b) == {"product": True, "campaign": True, "brand": True}
    assert campaign.missing_blocks(path_b) == []

    # ≤1 question per turn, and progress chips track the same three blocks
    progress = _artifacts(tid, "intake_progress")
    # No "brand.claims_confirmed" step: confirming claims GRANTS permission to
    # make them, it is not a toll on getting started. An unconfirmed brand just
    # has an empty approved list, so any claim used is unmapped and kill-flagged.
    assert [p["payload"]["next_field"] for p in progress[1:]] == [
        "product", "campaign", "brand", None, None]
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
    campaign.save_block(cid, "product", PRODUCT)

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
    assert campaign.cards_done(store.get_series(cid)["context"])["brand"] is False

    candidates = main.claims_extract(cid)
    assert "saves 4 hours a month" in " ".join(candidates["approved_claims"])
    assert store.get_series(cid)["context"]["brand"] is None  # candidates aren't saved either

    # Unconfirmed claims no longer block the rumination — but they are also not
    # APPROVED, so they buy the campaign nothing. The list stays unusable until
    # the user confirms it; that is where the compliance line sits now.
    campaign.save_block(cid, "campaign", CAMPAIGN)
    unconfirmed = campaign.save_block(cid, "brand", {"url": "https://ledger.example",
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
    saved = campaign.save_block(cid, "brand", {
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
    """Brand seat holds the kill flag: a persuasion claim outside the CONFIRMED
    approved list is killed, and a killed option never reaches the user."""
    dispatcher = threadkit._dispatcher(set())
    review = SeatReview.model_validate(campaign_mock.mock_seat(
        {"context": {"brand": {"approved_claims": ["files GST in 60 seconds"]}},
         "draft": {"concepts": [{"id": "o1", "claims_used": ["3x faster than QuickBooks"]}]}},
        dispatcher, "brand"))
    assert review.kill_recommendation and "3x faster than QuickBooks" in review.kill_recommendation

    seat_of = campaign_mock.mock_seat

    def brand_seat_kills(payload, dispatcher, seat):
        out = seat_of(payload, dispatcher, seat)
        if seat == "brand":
            out["kill_recommendation"] = "unsubstantiated claim: '3x faster than QuickBooks' is not confirmed"
        return out

    monkeypatch.setattr(campaign_mock, "mock_seat", brand_seat_kills)
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

    def unmapped_detail(payload, dispatcher):
        return {**detail_of(payload, dispatcher), "claims_used": ["3x faster than QuickBooks"]}

    monkeypatch.setattr(campaign_mock, "mock_campaign_detail", unmapped_detail)
    _act(tid, "o1", "approve")
    assert _artifacts(tid, "campaign_detail") == []     # no detail, no generation path
    assert _artifacts(tid, "escalation")[-1]["title"].startswith("The campaign detail kept failing")
    assert store.list_assets(tid) == []


# ------------------------------------ check 4: Skip = no style constraint at all --


def test_skip_on_templates_leaves_no_style_constraint(template_library):
    picked_style = template_library["style_descriptors"]

    cid, tid = _ruminated("Skipped")
    _act(tid, "o1", "approve")
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

    # …and the same flow WITH a template proves the descriptors do travel when picked
    cid2, tid2 = _ruminated("Styled")
    _act(tid2, "o1", "approve")
    _act(tid2, "templates", "pick_t1")
    styled = _artifacts(tid2, "campaign_detail")[-1]["payload"]["detail"]
    assert styled["style_ref"]["id"] == "t1"
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

    # the campaign surfaces are extra="forbid" — a smuggled override 422s
    campaign_id, _ = _filled("No overrides")
    for block, override in (("campaign", {"model": "fal-ai/some-other-model"}),
                            ("product", {"image_model": "x"}),
                            ("brand", {"video_model": "x"})):
        with pytest.raises(HTTPException) as exc:
            main.put_campaign_block(campaign_id, block, override)
        assert exc.value.status_code == 422
    with pytest.raises(ValidationError):
        UserEvent(thread_id="t1", type="action",
                  action={"artifact_id": "confirm", "event": "generate_single", "model": "x"})


def test_model_confirm_names_the_fixed_stack_whatever_the_user_types():
    cid, tid = _ruminated("Fixed stack", creative_type="video")
    _act(tid, "o1", "approve")

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
    assert _envelopes(tid)[-1]["question"] and "variants" in _envelopes(tid)[-1]["question"].lower()
    assert store.list_assets(tid) == []                 # the card alone renders nothing

    _act(tid, "confirm", "generate_single")
    assert _first_seq(tid, "creative_set") > confirm_seq
    assert store.list_assets(tid)


def test_a_variant_set_is_never_generated_without_an_explicit_count():
    cid, tid = _ruminated("Count")
    _act(tid, "o1", "approve")
    _act(tid, "detail", "generate_creative")

    _act(tid, "confirm", "generate_variants_0")         # button with no count
    assert store.list_assets(tid) == []
    assert _envelopes(tid)[-1]["question"] == "Two variants or three?"

    _text(tid, "give me variants")                      # typed, still no count
    assert store.list_assets(tid) == []
    assert _envelopes(tid)[-1]["text"] == "Variants need a count."

    _text(tid, "2 variants")                            # explicit count
    assert {i["variant_id"] for i in campaign._ws(tid)["items"]} == {"A", "B"}


def test_variants_carry_distinct_deltas_and_share_one_variant_group_id():
    cid, tid = _ruminated("Variants", creative_type="video")
    _act(tid, "o1", "approve")
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
    cards = store.list_ad_cards(cid)
    assert {c["variant_id"] for c in cards} == {"A", "B", "C"}
    assert len({c["variant_group_id"] for c in cards}) == 1
    assert all(c["variant_group_id"] for c in cards)
    assert len({c["naming"] for c in cards}) == 3


def test_single_shot_variants_are_not_identical_renders():
    cid, tid = _ruminated("Single shot", creative_type="image")
    _act(tid, "o1", "approve")
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
    _act(tid, "detail", "generate_creative")
    _act(tid, "confirm", "generate_single")
    _act(tid, "creative", "accept_all")

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
    from app.fal_client import estimate_cost, generate as real_generate

    def priced(kind, prompt, **kwargs):
        out = real_generate(kind, prompt, **kwargs)
        return {**out, "cost": estimate_cost(kind, duration_s=kwargs.get("duration_s", 4.0),
                                             tier=kwargs.get("tier", "final"))}

    monkeypatch.setattr(campaign, "generate", priced)

    cid, tid = _ruminated("Card cost", creative_type="video")
    _act(tid, "o1", "approve")
    _act(tid, "detail", "generate_creative")
    quoted = _artifacts(tid, "model_confirm")[-1]["payload"]["cost_single"]
    _act(tid, "confirm", "generate_single")
    _act(tid, "creative", "accept_all")

    card = store.list_ad_cards(cid)[0]
    assert store.campaign_spend(cid) == pytest.approx(quoted)      # we spent the quote
    assert card["total_cost_credits"] == pytest.approx(
        round(store.campaign_spend(cid) / threadkit.CREDIT_USD, 1))


# ------------------------------------------------ check 9: campaign lifecycle --


def test_campaign_lifecycle_lands_in_the_store():
    cid, tid = _filled("Lifecycle")
    assert store.get_campaign_status(cid) == "draft"

    campaign.begin_rumination(cid)
    assert store.get_campaign_status(cid) == "planned"

    _act(tid, "o1", "approve")
    _act(tid, "detail", "generate_creative")
    assert store.get_campaign_status(cid) == "planned"      # confirming spends nothing

    _act(tid, "confirm", "generate_single")
    assert store.get_campaign_status(cid) == "in_production"

    _act(tid, "creative", "accept_all")
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


# ----------------------------------------- check 10: blind council + one chair --


def test_council_seats_score_blind_and_the_chair_returns_the_v1_feedback(monkeypatch):
    seen: list[dict] = []
    dispatchers: list = []                 # held so identity comparison is meaningful
    seat_of = campaign_mock.mock_seat

    def spy(payload, dispatcher, seat):
        seen.append({"seat": seat, "payload": payload})
        dispatchers.append(dispatcher)
        return seat_of(payload, dispatcher, seat)

    monkeypatch.setattr(campaign_mock, "mock_seat", spy)
    cid, tid = _ruminated("Council")

    # The seats EXECUTE concurrently, so the order they finish in is not fixed —
    # that is why this asserts the set. Determinism is restored where it matters:
    # the chair node sorts to the canonical SEATS order before the chair sees
    # them (see test_the_chair_always_sees_seats_in_canonical_order), so the same
    # input yields the same chair input regardless of who answered first.
    assert sorted(s["seat"] for s in seen[:3]) == ["brand", "performance", "platform"]
    # v3: blindness used to be expressed as "a fresh dispatcher each, so each seat
    # sees its own retrieval slice". The council no longer retrieves at all, so
    # the same invariant is now the stronger statement — NO seat gets a
    # dispatcher, and there is no slice to leak between them. The other half of
    # blindness (no seat sees another's output) is asserted in the loop below and
    # is unchanged.
    assert dispatchers and all(d is None for d in dispatchers)
    for record in seen:
        payload = record["payload"]
        assert "seats" not in payload and "feedback" not in payload   # no sight of each other
        assert not any("kill_recommendation" in json.dumps(v, default=str)
                       for k, v in payload.items() if k != "draft")
        # no planner self-ratings travel to a seat
        assert all(c["element_scores"] == [] for c in payload["draft"]["concepts"])
        assert payload["options"] and payload["stage"] == "campaign_options"

    # …and the chair consolidates into the UNCHANGED v1 Feedback schema
    context = campaign._context_of(cid)
    shadow = campaign._shadow_context(context)
    options = CampaignOptions(options=list(campaign._ws(tid)["options"].values()))
    plan = campaign._shadow_plan(context, options, shadow)
    retrieved: set[str] = set()
    niche = campaign._niche_asset_count(shadow)
    feedback, reviews = campaign._run_council(context, shadow, options, plan, retrieved, niche)

    # The v1 CONSOLIDATION contract is untouched: concept_verdicts is still the
    # whole of what the chair decides. doctrine_version is an audit stamp the
    # server writes after validation (doctrine §8 — a Feedback that cannot name
    # its doctrine cannot be safely reused), never something the chair produces.
    assert isinstance(feedback, Feedback)
    assert set(Feedback.model_fields) == {"concept_verdicts", "doctrine_version"}
    assert feedback.doctrine_version == "3.0.0"
    assert len(reviews) == 3 and all(isinstance(r, SeatReview) for r in reviews)
    assert {r.seat for r in reviews} == {"performance", "brand", "platform"}

    family = objective_family(shadow.objective)
    required = set(ccs_mod.applicable_elements(family))
    assert {v.concept_id for v in feedback.concept_verdicts} == {c.id for c in plan.concepts}
    for verdict in feedback.concept_verdicts:
        assert {e.element for e in verdict.element_verdicts} >= required
        assert verdict.lenses.saturation.insufficient_data is (niche < 25)
        assert verdict.ccs_final == ccs_mod.compute_ccs(family, ccs_mod.final_ratings_of(verdict))
    # the v1 validator accepts it untouched — no campaign-shaped escape hatch
    assert validate_feedback(feedback, plan, shadow, campaign.rag,
                             retrieved_ids=retrieved, niche_asset_count=niche) is feedback

    # every seat's score is in the audit trail, not just in the chair's head
    logged = [a["detail"] for a in store.get_artifact_activity(tid, "council")]
    assert len(logged) >= 3
    assert {seat for seat in ("performance", "brand", "platform")
            if any(seat in detail for detail in logged)} == {"performance", "brand", "platform"}


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
    campaign.save_block(campaign_id, "brand", {
        "palette": ["#111111", "#222222"], "font": "Inter", "tagline": "t",
        "approved_claims": [], "banned_words": [], "claims_confirmed": True,
    })
    record = main.get_campaign(campaign_id)
    assert record["context"]["brand"]["policy_upload_id"] is None
    assert record["cards_done"]["brand"] is True          # the card completes

    # extraction without a policy doc still runs and says what it did
    out = main.claims_extract(campaign_id)
    assert any("policy" in note.lower() for note in out["notes"])

    # and the campaign is startable once the other two cards are filled
    campaign.save_block(campaign_id, "product", PRODUCT)
    campaign.save_block(campaign_id, "campaign", CAMPAIGN)
    assert campaign.missing_blocks(main.get_campaign(campaign_id)["context"]) == []


def test_an_unmapped_claim_still_kill_flags_when_the_approved_list_is_empty():
    """The floor the previous test stands on: confirming zero claims must not
    become a way to smuggle claims through unchecked."""
    campaign_id = campaign.start_campaign("Empty list floor")["campaign_id"]
    campaign.save_block(campaign_id, "brand", {
        "palette": ["#111111", "#222222"], "font": "Inter", "tagline": "t",
        "approved_claims": [], "banned_words": [], "claims_confirmed": True,
    })
    campaign.save_block(campaign_id, "product", PRODUCT)
    campaign.save_block(campaign_id, "campaign", CAMPAIGN)
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
    campaign.save_block(campaign_id, "product", PRODUCT)
    campaign.save_block(campaign_id, "campaign", CAMPAIGN)
    campaign.save_block(campaign_id, "brand", BRAND)
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


def test_the_chair_always_sees_seats_in_canonical_order(monkeypatch):
    """The seats EXECUTE concurrently, so they finish in whatever order the
    model answers (92s/175s/119s on the last real run). The chair is an LLM and
    LLMs are order-sensitive, so an unsorted join would make the same input
    produce different rumination run-to-run. The chair node sorts to the
    canonical SEATS order — parallel speed, deterministic input."""
    from app.agents import council as council_mod

    seen: list[list[str]] = []
    real = council_mod.run_council

    def spy(payload, plan, *a, seat_reviews=None, **k):
        if seat_reviews:
            seen.append([r.seat for r in seat_reviews])
        return real(payload, plan, *a, seat_reviews=seat_reviews, **k)

    monkeypatch.setattr(campaign, "run_council", spy)
    _ruminated("Canonical order")

    assert seen, "the chair never received pre-computed seat reviews"
    for order in seen:
        assert order == council_mod.SEATS, f"chair saw {order}, not {council_mod.SEATS}"


def test_one_product_image_is_enough_and_claims_do_not_block_the_start():
    """Two gates relaxed on purpose. A single pack shot locks consistency, and
    confirming claims GRANTS permission to make them rather than tolling the
    start. Neither relaxation may touch what the campaign is allowed to SAY."""
    campaign_id = campaign.start_campaign("One image, no claims")["campaign_id"]
    campaign.save_block(campaign_id, "product", {
        "name": "Solo", "description": "one shot is plenty", "image_upload_ids": ["up_1"]})
    campaign.save_block(campaign_id, "campaign", CAMPAIGN)
    campaign.save_block(campaign_id, "brand", {
        "palette": ["#111111", "#222222"], "font": "Inter", "tagline": "t",
        "approved_claims": [], "banned_words": [], "claims_confirmed": False})

    record = main.get_campaign(campaign_id)
    assert record["cards_done"] == {"product": True, "campaign": True, "brand": True}
    assert campaign.missing_blocks(record["context"]) == []      # startable
    assert main.start_campaign(campaign_id) == {"ok": True}


def test_an_unconfirmed_claims_list_is_candidates_not_permissions():
    """The floor the relaxation stands on. Dropping the start gate must NOT let
    a saved-but-unconfirmed list act as approved — otherwise the extractor would
    be granting itself authority."""
    campaign_id = campaign.start_campaign("Candidates only")["campaign_id"]
    campaign.save_block(campaign_id, "product", PRODUCT)
    campaign.save_block(campaign_id, "campaign", CAMPAIGN)
    saved = campaign.save_block(campaign_id, "brand", {
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


def test_a_brief_typed_at_the_path_step_is_taken_not_refused():
    """The user typed their whole brief at the two-path card and got 'Didn't
    catch a campaign command.' Asking them to pick a path AFTER they have
    described the campaign is asking a question they just answered."""
    campaign_id = campaign.start_campaign("Brief first")["campaign_id"]
    thread_id = campaign._campaign_thread_id(campaign_id)
    ws = campaign._ws(thread_id)

    parsed = campaign._parse("paths", "generate campaign for this product which is for parties", ws)
    assert parsed is not None, "a real brief was refused at the paths step"
    assert parsed["event"] == "brief"

    # the explicit path words still work
    assert campaign._parse("paths", "details", ws)["event"] == "path_structured"
    assert campaign._parse("paths", "help me", ws)["event"] == "path_conversational"


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
