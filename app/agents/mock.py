"""Deterministic mock agents (MOCK_LLM=1).

These are NOT canned strings: they call the real retrieval dispatcher so every
evidence citation is a live, resolvable source_id from the RAG service, and
their output flows through the same pydantic + server-side validation path as
real model output. Purpose: full-pipeline dev/tests without an API key.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

from app.tools import ToolDispatcher

ELEMENTS_BASE = [
    "hook_strength",
    "audience_alignment",
    "retention_structure",
    "differentiation",
    "distribution_triggers",
    "platform_format_fit",
    "creator_fit_feasibility",
]


def _niche_of(content_area: str) -> str:
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


def _fetch(dispatcher: ToolDispatcher, tool: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return json.loads(dispatcher.dispatch(tool, payload))


# ------------------------------------------------------------------- intake --


def mock_intake(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict[str, Any]:
    form = payload.get("form", {})
    uploads = payload.get("uploads", [])
    mode = form.get("mode", "series")
    cadence = form.get("cadence") or {}
    context: dict[str, Any] = {
        "name": form.get("name") or "Untitled",
        "mode": mode,
        "content_area": form.get("content_area") or "general",
        "description": form.get("description"),
        "objective": form.get("objective", "followers"),
        "target_audience": form.get("target_audience"),
        "platforms": form.get("platforms") or ["instagram_reels"],
        "cadence": cadence
        if cadence.get("type")
        else (
            {"type": "series", "posts_per_week": 3, "weeks": 2}
            if mode == "series"
            else {"type": "one_time", "concept_count": 6}
        ),
        "content_type": form.get("content_type", "text_video"),
        "style_notes": [],
        "upload_extractions": [],
        "brand_rules": [],
        "notes": [],
        "clarifying_questions": [],
    }
    for up in uploads:
        kind = up.get("kind", "other")
        context["upload_extractions"].append(
            {
                "filename": up.get("filename", "upload"),
                "kind": kind,
                "observations": [
                    "fast-cut pacing with bottom-third captions",
                    "energetic VO, no on-camera face",
                ]
                if kind == "past_post"
                else ["document parsed; tone rules and claims noted"],
            }
        )
        if kind == "past_post":
            context["style_notes"] = [
                "faceless screen-recording style",
                "captions always on, bottom third",
                "energetic, direct VO",
            ]
        if kind == "brief":
            context["brand_rules"] = [
                "no medical/efficacy claims without substantiation",
                "price point may be referenced",
            ]
    if not form.get("target_audience"):
        context["clarifying_questions"] = ["Who is the primary audience (age band + interest)?"]
    return context


# ------------------------------------------------------------------ planner --

_CONCEPT_SEEDS = [
    {
        "angle": "cost-slasher listicle",
        "title": '"{area}: I replaced a {price} workflow with 3 free tools"',
        "format": "listicle_demo",
        "hook_verbal": "I cancelled a {price} subscription — these 3 free {area} tools do it better",
        "first_frame": "printed invoice torn in half on camera, two objects, readable on mute",
        "cta": "Save this list before you pay again",
        "strength": "strong",
    },
    {
        "angle": "series diary",
        "title": '"Day {n} of {goal} — today it saved me from a mistake"',
        "format": "series_diary",
        "hook_verbal": "Day {n}: this almost cost me — here's what caught it",
        "first_frame": "day counter overlay slams in over a real workspace shot",
        "cta": "Follow for day {n2}",
        "strength": "good",
    },
    {
        "angle": "versus challenge",
        "title": '"Human vs AI: same task, who wins?"',
        "format": "challenge",
        "hook_verbal": "I gave the same job to a pro and to an AI",
        "first_frame": "split screen, countdown timer starts immediately",
        "cta": "Vote A or B in the comments",
        "strength": "strong",
    },
    {
        "angle": "myth-buster",
        "title": '"Stop doing {area} the hard way"',
        "format": "talking_head",
        "hook_verbal": "Everything you know about {area} is slightly wrong",
        "first_frame": "creator mid-gesture with bold caption overlay",
        "cta": "Comment the myth you believed",
        "strength": "weak",
    },
]


def mock_planner_concepts(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict[str, Any]:
    assert dispatcher is not None
    context = payload["context"]
    niche = _niche_of(context.get("content_area", ""))
    platform = (context.get("platforms") or ["instagram_reels"])[0]
    cadence = context["cadence"]
    slots = (
        cadence["posts_per_week"] * cadence["weeks"]
        if cadence["type"] == "series"
        else cadence["concept_count"]
    )

    assets = _fetch(dispatcher, "search_inspiration", {"query": context.get("content_area", ""), "niche": niche, "k": 8})
    if len(assets) < 2:
        assets = _fetch(dispatcher, "search_inspiration", {"query": "", "k": 8})
    chunks = _fetch(dispatcher, "search_corpus", {"query": "hook first frame retention", "k": 6})
    stats = _fetch(dispatcher, "get_benchmarks", {"niche": niche})
    trends = _fetch(dispatcher, "get_trends", {"niche": niche})
    _fetch(dispatcher, "get_profile", {})

    conversions = context["objective"] == "conversions"
    elements = ELEMENTS_BASE + (["persuasion_proof"] if conversions else [])

    def ev(tag: str, rec: dict[str, Any], claim: str) -> dict[str, Any]:
        return {"tag": tag, "source_id": rec["source_id"], "claim": claim, "as_of": rec.get("as_of")}

    def principle(claim: str) -> dict[str, Any]:
        return {"tag": "PRINCIPLE", "source_id": "model", "claim": claim, "as_of": None}

    start = date.today() + timedelta(days=3)
    concepts = []
    for i in range(slots):
        seed = _CONCEPT_SEEDS[i % len(_CONCEPT_SEEDS)]
        asset = assets[i % len(assets)]
        chunk = chunks[i % len(chunks)] if chunks else None
        stat = stats[i % len(stats)] if stats else None
        trend = trends[i % len(trends)] if trends else None
        weak = seed["strength"] == "weak"

        area = context.get("content_area", "your niche")
        fill = {"area": area, "price": "$2K/month", "n": str(i + 1), "n2": str(i + 2), "goal": context.get("description") or area}
        scores = []
        for element in elements:
            if element == "hook_strength":
                rating = "M" if weak else "H"
                evidence = [ev("REF", asset, f"proven hook pattern: {asset.get('hook_text', asset['title'])}")]
                if chunk:
                    evidence.append(ev("PRINCIPLE", chunk, "first-frame rules: readable on mute, <=3 objects"))
                scores.append(_score(element, rating, f"first frame: {seed['first_frame']}", evidence))
            elif element == "audience_alignment":
                evidence = []
                if stat:
                    evidence.append(ev("STAT", stat, stat.get("text", "niche benchmark")))
                if trend:
                    evidence.append(ev("TREND", trend, trend.get("text", "keyword velocity rising")))
                if evidence:
                    scores.append(_score(element, "H", "maps to a documented audience pain", evidence))
                else:
                    scores.append({"element": element, "addressed": False, "proposed_rating": None,
                                   "how_addressed": None, "why_not": "no evidence in DB", "evidence": []})
            elif element == "retention_structure":
                evidence = [ev("PRINCIPLE", chunk, "hook→foreshadow→beats→payoff formula")] if chunk else [principle("hook→foreshadow→beats→payoff structure")]
                scores.append(_score(element, "M" if weak else "H", "foreshadow + <=8s beats + payoff twist", evidence))
            elif element == "differentiation":
                scores.append(_score(element, "H", f"{seed['angle']} angle with a fresh twist", [ev("REF", asset, "closest performing reference, angle differs")]))
            elif element == "distribution_triggers":
                scores.append(_score(element, "M", seed["cta"], [principle("save-the-list utility + comment mechanic")]))
            elif element == "platform_format_fit":
                scores.append(_score(element, "H", f"native {seed['format']} on {platform}", [ev("STAT", stat, stat.get("text", "format benchmark"))] if stat else [principle("native format fit")]))
            elif element == "creator_fit_feasibility":
                scores.append(_score(element, "H" if not weak else "M", "zero-shoot format matches stated capacity and style", [principle("profile memory: faceless screen-rec style, no shoots")]))
            elif element == "persuasion_proof":
                scores.append(_score(element, "M", "on-screen proof + single CTA within approved claims", [ev("PRINCIPLE", chunk, "PAS + on-screen proof at the turn")] if chunk else [principle("PAS structure with proof element")]))

        concepts.append(
            {
                "id": f"c{i+1:02d}",
                "slot_date": str(start + timedelta(days=i * (7 // max(1, cadence.get('posts_per_week', 3))) if cadence["type"] == "series" else i)),
                "title": seed["title"].format(**fill),
                "description": f"{seed['angle'].capitalize()} for {area}: {asset.get('description', '')[:140]}",
                "creative_direction": f"{seed['format'].replace('_', ' ')}, {seed['first_frame'][:80]}, captions bottom-third, energetic VO",
                "hook": {"verbal": seed["hook_verbal"].format(**fill), "first_frame": seed["first_frame"]},
                "format": seed["format"],
                "platform": platform,
                "cta": seed["cta"].format(**fill),
                "effort": "S" if seed["format"] in ("listicle_demo", "talking_head") else "M",
                "asset_needs": ["screen recordings", "VO track"] if seed["format"] == "listicle_demo" else ["b-roll", "VO track"],
                "element_scores": scores,
            }
        )

    return {
        "series": {
            "objective": context["objective"],
            "north_star_metric": {"followers": "follower velocity", "impressions": "reach", "engagement": "saves + comments", "conversions": "CTR"}[context["objective"]],
            "pillar_mix": "60% core / 30% trend-adjacent / 10% experimental",
            "cadence": cadence,
            "checkpoint_date": str(start + timedelta(days=14)),
        },
        "concepts": concepts,
        "changes": [],
    }


def _score(element: str, rating: str, how: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "element": element,
        "addressed": True,
        "proposed_rating": rating,
        "how_addressed": how,
        "why_not": None,
        "evidence": evidence,
    }


# ----------------------------------------------------------------- feedback --


def mock_feedback(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict[str, Any]:
    assert dispatcher is not None
    plan = payload["plan"]
    context = payload["context"]
    only_ids = set(payload.get("only_concept_ids") or [])
    niche = _niche_of(context.get("content_area", ""))

    assets = _fetch(dispatcher, "search_inspiration", {"query": "saturation similar", "niche": niche, "k": 8})
    sat_source = assets[0]["source_id"] if assets else None

    verdicts = []
    for concept in plan["concepts"]:
        if only_ids and concept["id"] not in only_ids:
            continue
        weak = concept["format"] == "talking_head" and "slightly wrong" in concept["hook"]["verbal"]
        refined = bool(payload.get("refined"))
        element_verdicts = []
        for score in concept["element_scores"]:
            element = score["element"]
            proposed = score.get("proposed_rating")
            if not score["addressed"]:
                element_verdicts.append({"element": element, "verdict": "agree", "final_rating": None,
                                         "reason": "honest gap acknowledged", "evidence": [], "evidence_gap": True})
                continue
            if element == "hook_strength" and weak and not refined:
                element_verdicts.append({
                    "element": element, "verdict": "downgrade", "final_rating": "L",
                    "reason": "generic myth-buster opener — no stake, no specificity; dies at 3s",
                    "evidence": ([{"tag": "REF", "source_id": sat_source, "claim": "top performers open with a specific stake or physical interrupt", "as_of": None}] if sat_source else []),
                    "evidence_gap": not bool(sat_source),
                })
            elif element == "differentiation" and not weak and not refined and concept["format"] == "listicle_demo":
                element_verdicts.append({
                    "element": element, "verdict": "downgrade", "final_rating": "M",
                    "reason": "tool listicles saturated in this niche; the twist carries it",
                    "evidence": ([{"tag": "REF", "source_id": sat_source, "claim": "37 similar assets in DB last 90 days", "as_of": None}] if sat_source else []),
                    "evidence_gap": not bool(sat_source),
                })
            else:
                element_verdicts.append({"element": element, "verdict": "agree", "final_rating": proposed,
                                         "reason": None, "evidence": [], "evidence_gap": False})

        kill_flags = ["hook_low"] if (weak and not refined) else []
        fixes = (
            [{"priority": 1, "change": "Rewrite hook with a specific stake (number or enemy) + physical first-frame interrupt"},
             {"priority": 2, "change": "Add a foreshadow line before the list to cover the demo-dip"}]
            if weak and not refined
            else ([{"priority": 1, "change": "Lean the twist earlier — reveal by 0:06 to protect differentiation"}] if concept["format"] == "listicle_demo" else [])
        )
        verdicts.append({
            "concept_id": concept["id"],
            "element_verdicts": element_verdicts,
            "lenses": {
                "saturation": {"similar_count": 37 if concept["format"] == "listicle_demo" else 6,
                               "source_id": sat_source, "note": "similarity retrieval over inspiration DB"},
                "claims_safety": "no numeric product claims; safe" if context["objective"] != "conversions" else "numeric claims require brand substantiation doc before ship",
                "feasibility": "within stated capacity (no-shoot formats)",
                "platform_policy": "no policy risks detected",
            },
            "kill_flags": kill_flags,
            "fixes": fixes,
            "ccs_final": 0,  # server recomputes; model arithmetic is advisory
        })
    return {"concept_verdicts": verdicts}


# ------------------------------------------------------------ refine + opts --


def mock_planner_refine(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict[str, Any]:
    plan = json.loads(json.dumps(payload["plan"]))  # deep copy
    flagged = set(payload["flagged_concept_ids"])
    user_note = payload.get("user_feedback")
    changes = []
    for concept in plan["concepts"]:
        if concept["id"] not in flagged:
            continue
        concept["hook"]["verbal"] = f"I wasted ₹18,000 learning this about {payload['context'].get('content_area', 'this')} — 20 seconds saves you"
        concept["hook"]["first_frame"] = "hand slams a printed bill on the desk, bold caption with the number"
        concept["title"] = concept["title"].replace("Stop doing", "The ₹18,000 lesson in")
        for score in concept["element_scores"]:
            if score["element"] == "hook_strength" and score["addressed"]:
                score["proposed_rating"] = "H"
                score["how_addressed"] = "specific money stake + physical first-frame interrupt"
            if score["element"] == "retention_structure" and score["addressed"]:
                score["proposed_rating"] = "H"
        note = f" (user note: {user_note})" if user_note else ""
        changes.append(f"{concept['id']}: rewrote hook with specific stake + physical interrupt; strengthened foreshadow{note}")
    plan["changes"] = changes
    return plan


def mock_planner_options(payload: dict[str, Any], dispatcher: Optional[ToolDispatcher]) -> dict[str, Any]:
    out = []
    for concept in payload["concepts"]:
        ccs = concept.get("ccs_final", 80)
        out.append({
            "concept_id": concept["id"],
            "options": [
                {"option_id": "A", "angle_label": "as scored — direct angle",
                 "hook": concept["hook"],
                 "creative_direction_delta": "ship as planned", "ccs": ccs},
                {"option_id": "B", "angle_label": "story-led — personal cost narrative",
                 "hook": {"verbal": "The day I almost quit over this", "first_frame": "creator close-up, dim room, caption sets the low point"},
                 "creative_direction_delta": "same beats, narrative VO, slower first 5s then pace up", "ccs": max(0, ccs - 6)},
                {"option_id": "C", "angle_label": "challenge — head-to-head test",
                 "hook": {"verbal": "Human vs AI, same clip — who wins?", "first_frame": "split screen with countdown timer"},
                 "creative_direction_delta": "restructure as versus with a scorecard and comment vote", "ccs": max(0, ccs - 3)},
            ],
        })
    return {"concept_options": out}
