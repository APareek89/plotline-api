"""Concept Confidence Framework — §3.8.

The server recomputes CCS from the feedback agent's final ratings; the model
never gets to do its own arithmetic unchecked.
"""
from __future__ import annotations

from app.schemas import (
    Concept,
    ConceptVerdict,
    ElementName,
    ObjectiveFamily,
    Rating,
)

# v1 expert priors — recalibrate from performance memory once >=200 scored
# concepts have real results.
WEIGHTS: dict[ObjectiveFamily, dict[ElementName, int]] = {
    ObjectiveFamily.followers_reach: {
        "hook_strength": 25,
        "audience_alignment": 15,
        "retention_structure": 15,
        "differentiation": 15,
        "distribution_triggers": 10,
        "platform_format_fit": 10,
        "creator_fit_feasibility": 10,
    },
    ObjectiveFamily.engagement: {
        "hook_strength": 20,
        "audience_alignment": 15,
        "retention_structure": 15,
        "differentiation": 10,
        "distribution_triggers": 20,
        "platform_format_fit": 10,
        "creator_fit_feasibility": 10,
    },
    ObjectiveFamily.conversions: {
        "hook_strength": 25,
        "audience_alignment": 15,
        "retention_structure": 10,
        "differentiation": 10,
        "distribution_triggers": 0,  # scored for visibility, weightless for conversions
        "platform_format_fit": 10,
        "creator_fit_feasibility": 10,
        "persuasion_proof": 20,
    },
}

RATING_SCORE: dict[Rating, int] = {"H": 3, "M": 2, "L": 1}

QUALIFY_THRESHOLD = 70  # final CCS > 70 → qualified, 3 options generated
STRONG_THRESHOLD = 85  # >= 85 flagged "strong"


def applicable_elements(family: ObjectiveFamily) -> list[ElementName]:
    """Planner prompt: score every applicable element — 8 for conversions, else 7."""
    return list(WEIGHTS[family].keys())


def compute_ccs(family: ObjectiveFamily, final_ratings: dict[ElementName, "Rating | None"]) -> int:
    """CCS = sum(weight * score) / 3.  H=3 M=2 L=1, not-addressed (None)=0."""
    weights = WEIGHTS[family]
    total = 0
    for element, weight in weights.items():
        rating = final_ratings.get(element)
        score = RATING_SCORE[rating] if rating else 0
        total += weight * score
    return round(total / 3)


def final_ratings_of(verdict: ConceptVerdict) -> dict[ElementName, "Rating | None"]:
    return {ev.element: ev.final_rating for ev in verdict.element_verdicts}


def hook_gate_failed(verdict: ConceptVerdict) -> bool:
    """Hard gate: hook = Low → rework regardless of composite (hook is
    multiplicative, not additive)."""
    ratings = final_ratings_of(verdict)
    return ratings.get("hook_strength") in (None, "L")


def concept_status(family: ObjectiveFamily, verdict: ConceptVerdict) -> str:
    """qualified | strong | rework — kill flags block options regardless of score."""
    ccs = compute_ccs(family, final_ratings_of(verdict))
    if verdict.kill_flags or hook_gate_failed(verdict) or ccs <= QUALIFY_THRESHOLD:
        return "rework"
    return "strong" if ccs >= STRONG_THRESHOLD else "qualified"


# ------------------------------------ Addendum-01 §7.3: evidence coverage ---

PROVISIONAL_THRESHOLD = 40  # coverage % below this → PROVISIONAL badge, any CCS

_DATA_TAGS = ("REF", "STAT", "TREND")


def evidence_coverage(family: ObjectiveFamily, concept: "Concept") -> int:
    """% of applicable-element WEIGHT backed by data evidence (REF/STAT/TREND
    with a DB source) — PRINCIPLE/model opinions don't count. Shown beside CCS
    everywhere; < 40% marks the concept (and its plan) PROVISIONAL."""
    weights = WEIGHTS[family]
    total = sum(w for w in weights.values() if w > 0)
    backed = 0
    scores = {s.element: s for s in concept.element_scores}
    for element, weight in weights.items():
        if weight <= 0:
            continue
        score = scores.get(element)
        if score and score.addressed and any(
            e.tag in _DATA_TAGS and e.source_id != "model" for e in score.evidence
        ):
            backed += weight
    return round(100 * backed / total) if total else 0


def is_provisional(coverage_pct: int) -> bool:
    return coverage_pct < PROVISIONAL_THRESHOLD
