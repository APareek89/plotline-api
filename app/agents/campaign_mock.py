"""Deterministic Marketing-Studio mocks (MOCK_LLM=1 — tests only; the
product runs real models). Same discipline as mock.py: live retrieval via
the dispatcher, real validators downstream."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.tools import ToolDispatcher


def _fetch(d: ToolDispatcher, tool: str, payload: dict) -> list[dict]:
    return json.loads(d.dispatch(tool, payload))


_BLOCK_CUES = {
    "product": ("product", "we sell", "we make", "we built", "sku"),
    "campaign": ("campaign", "objective", "audience", "platform", "awareness", "traffic", "conversion"),
    "brand": ("brand", "logo", "palette", "font", "tagline", "colour", "color"),
}
_PLATFORM_CUES = {
    "instagram_reels": ("instagram reel", "ig reel", "reel"),
    "youtube_shorts": ("youtube short", "yt short", "shorts"),
    "tiktok": ("tiktok", "tik tok"),
    "instagram_feed": ("instagram feed", "ig feed", "instagram post"),
    "linkedin": ("linkedin",),
    "x": ("twitter", "x.com"),
}
_SPLIT = re.compile(r"\s+[—–-]\s+|\s*[,;:]\s*|\s+\bthat\b\s+")
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _tail(text: str, cue: str) -> Optional[str]:
    """Everything the user wrote after a cue phrase, up to the next separator."""
    match = re.search(rf"\b{cue}\b(?:\s+(?:is|are|:))?\s*(.+)", text, re.IGNORECASE)
    if not match:
        return None
    return _SPLIT.split(match.group(1).strip(), maxsplit=1)[0].strip(" .")


def mock_campaign_intake(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict:
    """Path b stand-in: fills at most the ONE block the message names, from
    the user's own words. A block whose required fields aren't in the message
    stays unfilled — the progress card then re-asks rather than the mock
    inventing an audience. claims_confirmed is never touched here: only the
    user's Brand-card PUT sets it."""
    context = dict(payload["context"])
    message = (payload.get("message") or "").strip()
    low = message.lower()
    for block, cues in _BLOCK_CUES.items():
        if context.get(block) is not None or not any(cue in low for cue in cues):
            continue
        filled = _intake_block(block, message, low)
        if filled is not None:
            context[block] = filled
        break
    return context


def _intake_block(block: str, message: str, low: str) -> Optional[dict[str, Any]]:
    """None = the message named the block but not the fields the schema
    requires, so nothing is written."""
    if block == "product":
        tail = _tail(message, "product") or _tail(message, "we sell") or _tail(message, "we make")
        if not tail:
            return None
        return {"name": tail[:60], "description": message}
    if block == "campaign":
        objective = next((o for o in ("awareness", "traffic", "conversion") if o in low), None)
        audience = _tail(message, "audience") or _tail(message, "for") or _tail(message, "targeting")
        platforms = [p for p, cues in _PLATFORM_CUES.items() if any(c in low for c in cues)]
        if not (objective and audience and platforms):
            return None  # all three are required — a half-parsed block is a guess
        return {
            "objective": "conversions" if objective == "conversion" else objective,
            "target_audience": audience,
            "platforms": platforms,
            "creative_type": "video" if "video" in low else "image",
        }
    brand: dict[str, Any] = {}  # every brand field is optional in the schema
    url = re.search(r"https?://\S+", message)
    if url:
        brand["url"] = url.group(0).rstrip(".,")
    palette = _HEX.findall(message)
    if palette:
        brand["palette"] = palette
    for field in ("font", "tagline"):
        value = _tail(message, field)
        if value:
            brand[field] = value
    return brand  # may be empty: the block exists, the ✓ still waits on the confirm


def mock_campaign_options(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict:
    assert dispatcher is not None
    ctx = payload["context"]
    product = (ctx.get("product") or {}).get("name", "the product")
    assets = _fetch(dispatcher, "search_inspiration", {"query": product, "k": 4})
    ev = (
        [{"tag": "REF", "source_id": assets[0]["source_id"],
          "claim": f"nearest performing neighbor: {assets[0].get('title', '')[:60]}", "as_of": None}]
        if assets else []
    )
    mk = lambda i, name, desc, story: {
        "option_id": f"o{i}", "name_line": name, "description": desc,
        "storyline": story, "objective_echo": (ctx.get("campaign") or {}).get("objective", "awareness"),
        "why_it_fits": "grounded in retrieved neighbors" if ev else "no evidence in DB",
        "evidence": ev,
    }
    return {"options": [
        mk(1, f"{product}: the receipt test", f"Prove {product} on camera with a real invoice torn at 0:02.",
           "Open on the bill, demo the replacement live, end on the twist that this ad was made with it."),
        mk(2, f"{product} vs the old way", "Head-to-head against the incumbent with a visible scorecard.",
           "Split screen, same task both sides, timer running, comment-vote close."),
    ]}


def mock_campaign_detail(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict:
    ctx = payload["context"]
    ctype = (ctx.get("campaign") or {}).get("creative_type", "image")
    claims = (ctx.get("brand") or {}).get("approved_claims", [])[:1]
    style = payload.get("template") or None
    descr = (style or {}).get("style_descriptors", [])
    suffix = f", style: {', '.join(descr)}" if descr else ""
    shots = (
        [{"slot": "shot_01", "duration_s": 4.0,
          "visual_prompt": f"product hero on dark desk, invoice torn on camera{suffix}",
          "vo_or_copy": "I cancelled the old bill — 60 seconds shows why."},
         {"slot": "shot_02", "duration_s": 6.0,
          "visual_prompt": f"screen recording of the product doing the task{suffix}",
          "vo_or_copy": "Watch it do the whole job."}]
        if ctype == "video"
        else [{"slot": "slide_01", "duration_s": None,
               "visual_prompt": f"product hero shot, bold number overlay, brand palette{suffix}",
               "vo_or_copy": "The $200/month habit you can cancel today."}]
    )
    return {"creative_type": ctype, "shots": shots,
            "copy_primary": "Receipts on screen. Save this before your next bill.",
            "cta": "Try it free", "claims_used": claims,
            "style_ref": style, "version": payload.get("version", 1),
            "changes": payload.get("changes", [])}


# a persuasion claim is a CHECKABLE one, so the brand lens reads only cue-
# bearing lines — flagging every sentence of ad copy would kill every option.
_CLAIM_CUE = re.compile(
    r"\b(\d+ ?%|\d+x|clinically|proven|guarantee\w*|certified|fastest|cheapest|"
    r"#1|no\.? ?1|up to \d+|\d+ (?:hours?|minutes?|seconds?|days?))\b", re.IGNORECASE)


def _unmapped_claim(payload: dict[str, Any]) -> Optional[str]:
    """Persuasion claim outside the CONFIRMED list, read off what the seat is
    actually handed: claims_used when a draft carries any, plus the verbatim
    option copy — the shadow Plan's v1 Concepts have no claims_used field, so
    at the options stage the copy is the only place a claim is visible."""
    confirmed = [c.lower() for c in (payload["context"].get("brand") or {}).get("approved_claims", []) if c]
    for concept in payload.get("draft", {}).get("concepts", []):
        for claim in concept.get("claims_used", []):
            if claim.lower() not in confirmed:
                return claim[:60]
    for option in payload.get("options", []):
        blob = " ".join(str(option.get(k) or "")
                        for k in ("name_line", "description", "storyline", "why_it_fits"))
        for sentence in re.split(r"[.;\n]", blob):
            line = sentence.strip()
            if _CLAIM_CUE.search(line) and not any(c in line.lower() for c in confirmed):
                return line[:60]
    return None


def _principle(claim: str) -> list[dict[str, Any]]:
    return [{"tag": "PRINCIPLE", "source_id": "model", "claim": claim, "as_of": None}]


def mock_seat(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher], seat: str) -> dict:
    """v3: a doctrine seat, so no dispatcher and no retrieved citation.

    `dispatcher` stays in the signature because run_agent calls every mock the
    same way; it is now always None. The mock cites PRINCIPLE/model like the real
    seats do — a mock that could not pass validate_seat_review would stop proving
    anything about the path it stands in for.
    """
    base = [
        {"element": "hook_strength", "rating": "H",
         "reason": f"{seat}: hook carries a concrete stake",
         "evidence": _principle("an opening earns attention through tension or specificity")},
        {"element": "audience_alignment", "rating": "H",
         "reason": f"{seat}: maps to the stated audience",
         "evidence": _principle("creative moves someone from the belief they already hold")},
    ]
    unmapped = _unmapped_claim(payload) if seat == "brand" else None
    kill = f"unsubstantiated_claim: {unmapped}" if unmapped else None
    return {"seat": seat, "element_scores": base, "kill_recommendation": kill,
            "policy_check_required": False, "policy_notes": [], "fixes": []}


# ------------------------------------------------- v3 stage mocks ------------
# Deterministic stand-ins that go through the SAME schema + server validators the
# real agents do. A mock that could not pass its own gate would prove nothing.


def mock_campaign_brief(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict:
    ctx = payload["context"]
    camp = ctx.get("campaign") or {}
    brand = ctx.get("brand") or {}
    product = ctx.get("product") or {}
    claims = list(brand.get("approved_claims") or []) if brand.get("claims_confirmed") else []
    ratio = "9:16" if camp.get("creative_type") == "video" else "4:5"
    return {
        "objective": camp.get("objective", "conversions"),
        "audience": camp.get("target_audience", ""),
        "platforms": list(camp.get("platforms") or []),
        "creative_type": camp.get("creative_type", "image"),
        "target_metric": None,
        "audience_current_belief": f"they assume every option in this category is the same",
        "single_message": (claims[0] if claims else product.get("name", "the product"))[:160],
        "brand_role": f"{product.get('name', 'the product')} does the work on screen",
        "offer_cta": "Start now",
        "aspect_ratios": [ratio],
        "duration_s": 6.0 if camp.get("creative_type") == "video" else None,
        "languages": ["en-IN"],
        "multi_format_policy": "safe_area",
        "mandatories": [], "guardrails": list(brand.get("banned_words") or []),
        "budget_credits": None,
        "proof_points": claims[:1],
    }


def mock_hook_rack(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict:
    brief = payload.get("brief") or {}
    message = brief.get("single_message") or "the product does the work"
    line = " ".join(message.split()[:6]) or "watch this"
    return {
        "language": (brief.get("languages") or ["en-IN"])[0],
        "body": [{"slot": "beat_01", "t_in": 2.5, "t_out": 6.0, "text": line,
                  "emotion": "matter of fact", "words": 0, "wps": 0,
                  "wps_verdict": "pass", "proposed_fix": None, "claim_refs": []}],
        "hooks": [{"slot": "hook", "t_in": 0.0, "t_out": 2.5, "text": line,
                   "emotion": "dry, certain", "words": 0, "wps": 0,
                   "wps_verdict": "pass", "proposed_fix": None, "claim_refs": []}],
        "selected_hook_slot": "hook", "loanwords_kept": [], "total_duration_s": 0,
    }


def mock_shot_board(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict:
    brief = payload.get("brief") or {}
    ctype = brief.get("creative_type", "image")
    claims = list(brief.get("proof_points") or [])
    route = "video" if ctype == "video" else "image_final"
    def shot(slot: str, beat: str, dialogue: Optional[str], camera: str) -> dict:
        return {
            "slot": slot, "duration_s": 3.0, "beat": beat, "dialogue_ref": dialogue,
            "action": "slow lift", "camera": camera, "shot_size": "MCU",
            "emotion": "quiet interest",
            "cast_refs": [], "product_refs": ["@product"], "env_refs": [],
            "keyframe_prompt": f"{beat}, label legible",
            "motion_prompt": "gentle push in" if ctype == "video" else "",
            "model_route": route,
            "route_reason": "legible on-pack text" if ctype == "image" else "dialogue to camera",
            "slots_used": 0, "est_cost_usd": 0.08,
        }

    # A video board carries TWO shots: one-shot video makes the variant counts
    # untestable, and a spot with a hook and no payoff is not a spot.
    shots = [shot("shot_01", "the product is held to the light",
                  "hook" if ctype == "video" else None, "push_in")]
    if ctype == "video":
        shots.append(shot("shot_02", "the result, close", "beat_01", "static"))

    return {
        "creative_type": ctype,
        "shots": shots,
        "copy_primary": brief.get("single_message", "") or "the product does the work",
        "cta": brief.get("offer_cta", "Start now"),
        "claims_used": claims, "style_block_id": payload.get("style_block_id"),
        "est_total_usd": 0, "version": 1, "changes": [],
    }


def mock_canon_plan(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict:
    board = payload.get("board") or {}
    wanted: list[str] = []
    for shot in board.get("shots", []):
        for key in ("cast_refs", "product_refs", "env_refs"):
            for ref in shot.get(key) or []:
                if ref not in wanted:
                    wanted.append(ref)
    kind_of = {"cast_refs": "character", "product_refs": "product", "env_refs": "environment"}
    sheets = []
    for ref in wanted:
        kind = next((kind_of[k] for shot in board.get("shots", [])
                     for k in kind_of if ref in (shot.get(k) or [])), "product")
        sheets.append({"id": ref, "kind": kind, "label": ref.lstrip("@"),
                       "brief": f"canon for {ref}", "asset_ids": [], "coverage": {},
                       "locks": ["proportions", "label text"] if kind == "product" else ["wardrobe"],
                       "slot_cost": 1, "risk_notes": [], "rights": "fictional",
                       "consent_ref": None, "native_review": {}, "version": 1})
    return {"sheets": sheets}
