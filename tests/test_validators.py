"""Server-side validator tests: dead citations, refine diffs, feedback completeness."""
import pytest

from app.rag_client import RagClient
from app.schemas import CreatorContext, Feedback, OptionsOutput, Plan
from app.validators import AgentValidationError, validate_feedback, validate_options, validate_plan


def _context(objective="followers", slots=(2, 1)):
    return CreatorContext.model_validate(
        {
            "name": "Test",
            "mode": "series",
            "content_area": "ai_tools",
            "objective": objective,
            "platforms": ["instagram_reels"],
            "cadence": {"type": "series", "posts_per_week": slots[0], "weeks": slots[1]},
            "content_type": "text_video",
        }
    )


def _element(el, rating="H", source_id="asset:A118"):
    return {
        "element": el,
        "addressed": True,
        "proposed_rating": rating,
        "how_addressed": "addressed concretely",
        "why_not": None,
        "evidence": [{"tag": "REF", "source_id": source_id, "claim": "proven pattern", "as_of": None}],
    }


ELEMENTS_7 = [
    "hook_strength",
    "audience_alignment",
    "retention_structure",
    "differentiation",
    "distribution_triggers",
    "platform_format_fit",
    "creator_fit_feasibility",
]


def _concept(cid="c01", source_id="asset:A118"):
    return {
        "id": cid,
        "slot_date": None,
        "title": "T",
        "description": "D",
        "creative_direction": "screen recording with an on-screen timer as the receipt",
        "hook": {"verbal": "I cancelled a $200 tool — the invoice is the proof", "first_frame": "f"},
        "format": "listicle_demo",
        "platform": "instagram_reels",
        "cta": "save",
        "effort": "S",
        "asset_needs": [],
        "element_scores": [_element(el, source_id=source_id) for el in ELEMENTS_7],
    }


def _plan(concepts):
    return Plan.model_validate(
        {
            "series": {
                "objective": "followers",
                "north_star_metric": "follower velocity",
                "pillar_mix": None,
                "cadence": {"type": "series", "posts_per_week": 2, "weeks": 1},
                "checkpoint_date": None,
            },
            "concepts": concepts,
            "changes": [],
        }
    )


def test_dead_citation_fails_whole_output():
    plan = _plan([_concept("c01"), _concept("c02", source_id="asset:DOES_NOT_EXIST")])
    with pytest.raises(AgentValidationError, match="unresolvable"):
        validate_plan(plan, _context(), RagClient())


def test_small_sample_stat_is_a_dead_citation():
    # stat:S99 exists in fixtures but n=12 < 30 — never publishable, so unresolvable
    plan = _plan([_concept("c01"), _concept("c02", source_id="stat:S99")])
    with pytest.raises(AgentValidationError, match="unresolvable"):
        validate_plan(plan, _context(), RagClient())


def test_slot_count_enforced():
    plan = _plan([_concept("c01")])  # cadence demands 2
    with pytest.raises(AgentValidationError, match="cadence requires 2 slots"):
        validate_plan(plan, _context(), RagClient())


def test_refine_may_touch_only_flagged():
    before = _plan([_concept("c01"), _concept("c02")])
    after_raw = before.model_dump(mode="json")
    after_raw["concepts"][1]["title"] = "sneaky edit"  # c02 not flagged
    after = Plan.model_validate(after_raw)
    with pytest.raises(AgentValidationError, match="unflagged concept c02"):
        validate_plan(after, _context(), RagClient(), previous_plan=before, flagged_concept_ids={"c01"})


def test_refine_flagged_edit_passes():
    before = _plan([_concept("c01"), _concept("c02")])
    after_raw = before.model_dump(mode="json")
    after_raw["concepts"][0]["title"] = "improved"
    after = Plan.model_validate(after_raw)
    validate_plan(after, _context(), RagClient(), previous_plan=before, flagged_concept_ids={"c01"})


def _verdicts(cid, elements=ELEMENTS_7):
    return {
        "concept_id": cid,
        "element_verdicts": [
            {"element": el, "verdict": "agree", "final_rating": "H", "reason": None, "evidence": [], "evidence_gap": False}
            for el in elements
        ],
        "lenses": {
            "saturation": {"similar_count": 3, "source_id": "asset:A118", "note": None},
            "claims_safety": "ok",
            "feasibility": "ok",
            "platform_policy": "ok",
        },
        "kill_flags": [],
        "fixes": [],
        "ccs_final": 100,
    }


def test_feedback_missing_element_invalid():
    plan = _plan([_concept("c01"), _concept("c02")])
    fb = Feedback.model_validate(
        {"concept_verdicts": [_verdicts("c01"), _verdicts("c02", ELEMENTS_7[:-1])]}
    )
    with pytest.raises(AgentValidationError, match="element_verdicts missing"):
        validate_feedback(fb, plan, _context(), RagClient())


def test_feedback_missing_concept_invalid():
    plan = _plan([_concept("c01"), _concept("c02")])
    fb = Feedback.model_validate({"concept_verdicts": [_verdicts("c01")]})
    with pytest.raises(AgentValidationError, match="missing verdicts"):
        validate_feedback(fb, plan, _context(), RagClient())


def test_server_recomputes_ccs():
    plan = _plan([_concept("c01"), _concept("c02")])
    fb = Feedback.model_validate({"concept_verdicts": [_verdicts("c01"), _verdicts("c02")]})
    fb.concept_verdicts[0].ccs_final = 5  # model lied; server recomputes
    validate_feedback(fb, plan, _context(), RagClient())
    assert fb.concept_verdicts[0].ccs_final == 100


def test_options_exactly_three_distinct():
    with pytest.raises(Exception, match="distinct"):
        OptionsOutput.model_validate(
            {
                "concept_options": [
                    {
                        "concept_id": "c01",
                        "options": [
                            {"option_id": "A", "angle_label": "same", "hook": {"verbal": "v", "first_frame": "f"}, "creative_direction_delta": "d", "ccs": 80},
                            {"option_id": "B", "angle_label": "same", "hook": {"verbal": "v", "first_frame": "f"}, "creative_direction_delta": "d", "ccs": 80},
                            {"option_id": "C", "angle_label": "other", "hook": {"verbal": "v", "first_frame": "f"}, "creative_direction_delta": "d", "ccs": 80},
                        ],
                    }
                ]
            }
        )


def test_options_only_for_qualified():
    opts = OptionsOutput.model_validate(
        {
            "concept_options": [
                {
                    "concept_id": "c99",
                    "options": [
                        {"option_id": "A", "angle_label": "a", "hook": {"verbal": "v", "first_frame": "f"}, "creative_direction_delta": "d", "ccs": 80},
                        {"option_id": "B", "angle_label": "b", "hook": {"verbal": "v", "first_frame": "f"}, "creative_direction_delta": "d", "ccs": 74},
                        {"option_id": "C", "angle_label": "c", "hook": {"verbal": "v", "first_frame": "f"}, "creative_direction_delta": "d", "ccs": 77},
                    ],
                }
            ]
        }
    )
    with pytest.raises(AgentValidationError, match="non-qualified"):
        validate_options(opts, {"c01"})


def test_principle_must_use_model_source_or_resolvable_id():
    from app.schemas import Evidence

    Evidence.model_validate({"tag": "PRINCIPLE", "source_id": "model", "claim": "c", "as_of": None})
    with pytest.raises(Exception):
        Evidence.model_validate({"tag": "STAT", "source_id": "model", "claim": "c", "as_of": None})
