"""Deterministic creative-director mock (MOCK_LLM=1) — Addendum-02.

Builds the §02 sequence deterministically from the concept + chosen option:
confidence beats, route recommendation, script package (v1 §04 schemas),
voice options, keyframe prompts with locks, captions per platform. Real mode
swaps these for creative_director.md-driven model calls; the driver, cost
lines, and validators are identical either way.
"""
from __future__ import annotations

from typing import Any, Optional

from app import config
from app.media import estimate_cost

LOCKS = [
    "identity: faceless creator, hands + screen only",
    "wardrobe: n/a (no on-camera talent)",
    "lighting: dark desk, single screen-glow key",
]


def route_recommendation(concept: dict[str, Any]) -> dict[str, Any]:
    fmt = concept.get("format", "")
    if fmt in ("listicle_demo", "challenge"):
        route, reason = "keyframe_cuts", "fits the multi-beat structure, cheapest per shot"
    elif fmt in ("talking_head", "series_diary"):
        route, reason = "one_take", "single continuous beat reads more human"
    else:
        route, reason = "keyframe_cuts", "default: cut boundaries hide model seams"
    return {"route": route, "reason": reason, "alternatives": ["one_take", "interpolated"]}


def beats_of(concept: dict[str, Any], option: dict[str, Any]) -> list[str]:
    hook = (option.get("hook") or concept["hook"])["verbal"]
    return [
        f"open: {hook}",
        f"build: {concept['creative_direction'][:80]}",
        "turn: the receipt lands on screen",
        f"close: {concept['cta']}",
    ]


def build_script(concept: dict[str, Any], option: dict[str, Any], route: str) -> dict[str, Any]:
    hook = (option.get("hook") or concept["hook"])
    beats = beats_of(concept, option)
    shots = [
        {"shot_id": "shot_01", "duration_s": 4.0,
         "keyframe_prompt": f"{hook['first_frame']}, {concept['platform']} vertical, high contrast",
         "motion_prompt": "slow push-in, hands enter frame", "vo_segment": hook["verbal"],
         "boundary": "cut"},
        {"shot_id": "shot_02", "duration_s": 8.0,
         "keyframe_prompt": "screen recording close-up, timeline auto-cutting, cursor moving fast",
         "motion_prompt": "screen capture pans across the edit timeline",
         "vo_segment": "Watch it find every dead second — no scrubbing, no guesswork.",
         "boundary": "cut"},
        {"shot_id": "shot_03", "duration_s": 8.0,
         "keyframe_prompt": "second tool generating captions, bottom-third captions visible, dark UI",
         "motion_prompt": "UI elements animate in as captions populate",
         "vo_segment": "Captions, cut-downs, color — each tool takes a job off your plate.",
         "boundary": "cut" if route == "keyframe_cuts" else "interpolate"},
        {"shot_id": "shot_04", "duration_s": 6.0,
         "keyframe_prompt": "creator workspace wide shot, finished reel playing on phone propped on desk",
         "motion_prompt": "hold, phone screen glows, subtle dolly out",
         "vo_segment": f"The twist: this exact video? Tool three edited it. {concept['cta']}",
         "boundary": "cut"},
    ]
    total_s = sum(s["duration_s"] for s in shots)
    est = (
        sum(estimate_cost("image", tier="final") for _ in shots)
        + estimate_cost("video", duration_s=total_s)
        + estimate_cost("audio", chars=sum(len(s["vo_segment"] or "") for s in shots), tier="final")
    )
    return {
        "beats": beats,
        "vo_script": " ".join(s["vo_segment"] for s in shots if s["vo_segment"]),
        "shots": shots,
        "consistency_plan": {
            "identity_pack_ids": [],
            "wardrobe_lock": LOCKS[1],
            "lighting_lock": LOCKS[2],
        },
        "route": route,
        "closing_shot_option_id": None,
        "cost_estimate_credits": round(est / 0.10, 1),  # 1 credit = $0.10
    }


def voice_options() -> dict[str, Any]:
    return {
        "voices": [
            {"id": "voice_a", "label": '"Asha" — energetic, fast', "tier": "final", "preview_url": None},
            {"id": "voice_b", "label": '"Neel" — calm, deliberate', "tier": "final", "preview_url": None},
        ]
    }


def frame_prompt(shot: dict[str, Any], ratio: str) -> str:
    locks = "; ".join(LOCKS)
    return f"{shot['keyframe_prompt']}. {locks}. {ratio} vertical composition, readable on mute."


def edit_shot_vo(shot: dict[str, Any], note: str) -> str:
    """§02 step 5: named-shot edit only, diffable. Deterministic 'punchier' pass."""
    base = shot["vo_segment"] or ""
    tightened = base.replace("Watch it find every dead second — no scrubbing, no guesswork.",
                             "Every dead second — gone. Zero scrubbing.")
    if tightened == base:
        tightened = base.rstrip(".") + " — faster than you can scrub."
    return tightened


def captions(concept: dict[str, Any], platforms: list[str], profile: dict[str, Any]) -> dict[str, Any]:
    hook = concept["hook"]["verbal"]
    area = concept.get("format", "reel").replace("_", " ")
    tone = ", ".join(profile.get("tone_rules", [])[:2]) or "direct"
    variants: dict[str, str] = {}
    for p in platforms:
        if p == "youtube_shorts":
            variants[p] = f"{hook} — full breakdown in 30 seconds. #shorts"
        elif p == "linkedin":
            variants[p] = f"{hook}\n\nThe workflow, step by step, in the video. What would you automate first?"
        else:
            variants[p] = f"{hook} 🎬 Save this before your next edit. ({tone}, receipts on screen)"
    return {
        "hook_line": hook,
        "caption_variants": variants,
        "hashtags": ["#aitools", "#contentcreator", "#videoediting", "#creatoreconomy",
                     "#editing", "#workflow", "#ai", "#reels"][:8],
        "cta": concept["cta"],
        "alt_text": f"{area} video: {concept['title'][:120]}",
    }


def end_card_options(profile: dict[str, Any]) -> list[str]:
    niche = profile.get("niche") or "this niche"
    return [
        "New tools every week",
        "Follow for the next 3",
        f"Decode {niche} with me",
    ]
