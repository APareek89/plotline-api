"""CCS engine tests — anchored to the two worked examples in PRD §3.8."""
from app.ccs import (
    QUALIFY_THRESHOLD,
    STRONG_THRESHOLD,
    WEIGHTS,
    applicable_elements,
    compute_ccs,
    concept_status,
    hook_gate_failed,
)
from app.schemas import ConceptVerdict, Lenses, ObjectiveFamily, SaturationLens


def test_weights_sum_to_100():
    for family, weights in WEIGHTS.items():
        assert sum(weights.values()) == 100, family


def test_applicable_counts():
    assert len(applicable_elements(ObjectiveFamily.followers_reach)) == 7
    assert len(applicable_elements(ObjectiveFamily.engagement)) == 7
    assert len(applicable_elements(ObjectiveFamily.conversions)) == 8


def test_ria_example_scores_92():
    # PRD example 1: followers objective — Hook H, Audience H, Retention H,
    # Differentiation M (red-team downgrade), Distribution M, Platform H, Creator H → 92 SHIP
    ratings = {
        "hook_strength": "H",
        "audience_alignment": "H",
        "retention_structure": "H",
        "differentiation": "M",
        "distribution_triggers": "M",
        "platform_format_fit": "H",
        "creator_fit_feasibility": "H",
    }
    assert compute_ccs(ObjectiveFamily.followers_reach, ratings) == 92


def test_kabir_example_scores_87():
    # PRD example 2: conversions — Hook H, Audience H, Persuasion M (downgrade),
    # Retention M, Platform H, Differentiation M, Creator H → 87
    ratings = {
        "hook_strength": "H",
        "audience_alignment": "H",
        "persuasion_proof": "M",
        "retention_structure": "M",
        "platform_format_fit": "H",
        "differentiation": "M",
        "creator_fit_feasibility": "H",
    }
    assert compute_ccs(ObjectiveFamily.conversions, ratings) == 87


def test_not_addressed_scores_zero():
    ratings = {"hook_strength": "H"}  # everything else missing → 0
    assert compute_ccs(ObjectiveFamily.followers_reach, ratings) == 25


def _verdict(ratings, kill_flags=()):
    return ConceptVerdict(
        concept_id="c01",
        element_verdicts=[
            {"element": el, "verdict": "agree", "final_rating": r, "reason": None, "evidence": [], "evidence_gap": False}
            for el, r in ratings.items()
        ],
        lenses=Lenses(
            saturation=SaturationLens(similar_count=0, source_id=None, note=None),
            claims_safety="ok",
            feasibility="ok",
            platform_policy="ok",
        ),
        kill_flags=list(kill_flags),
        fixes=[],
        ccs_final=0,
    )


FULL_H = {
    "hook_strength": "H",
    "audience_alignment": "H",
    "retention_structure": "H",
    "differentiation": "H",
    "distribution_triggers": "H",
    "platform_format_fit": "H",
    "creator_fit_feasibility": "H",
}


def test_hook_low_hard_gate():
    ratings = dict(FULL_H, hook_strength="L")
    verdict = _verdict(ratings)
    assert hook_gate_failed(verdict)
    # composite is high, but the hard gate forces rework
    assert compute_ccs(ObjectiveFamily.followers_reach, ratings) > QUALIFY_THRESHOLD
    assert concept_status(ObjectiveFamily.followers_reach, verdict) == "rework"


def test_kill_flag_blocks_options():
    verdict = _verdict(FULL_H, kill_flags=["unsubstantiated_claim"])
    assert concept_status(ObjectiveFamily.followers_reach, verdict) == "rework"


def test_status_bands():
    assert concept_status(ObjectiveFamily.followers_reach, _verdict(FULL_H)) == "strong"
    mid = dict(FULL_H, hook_strength="M", differentiation="M", retention_structure="M")
    status = concept_status(ObjectiveFamily.followers_reach, _verdict(mid))
    ccs = compute_ccs(ObjectiveFamily.followers_reach, mid)
    assert QUALIFY_THRESHOLD < ccs < STRONG_THRESHOLD
    assert status == "qualified"
    low = {k: "M" for k in FULL_H} | {"hook_strength": "M", "audience_alignment": "L", "differentiation": "L"}
    assert compute_ccs(ObjectiveFamily.followers_reach, low) <= QUALIFY_THRESHOLD
    assert concept_status(ObjectiveFamily.followers_reach, _verdict(low)) == "rework"
