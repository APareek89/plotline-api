"""The reviewer — one Marketing Expert who judges the planner's draft.

v3.2 (owner decision 2026-08-25): "after planner only one agent (Marketing
expert) reviewing the plan and giving feedback to planner for refinement."

WHAT THIS REPLACED, AND WHY
---------------------------
Three blind seats plus a chair. Four LLM calls whose combined product was one
`Feedback` object: the seats each judged through one lens, and the chair's whole
job was to merge them back together. On the production models that shape cost
most of a ~15 minute rumination — roughly two minutes per seat and nearly eight
for the chair, because consolidating three full reviews is a big generation.

The Marketing Expert carries all three lenses and emits `Feedback` directly, so
the consolidation step is removed rather than made faster. The refine loop, the
CCS recomputation, the kill flags and every validator are untouched: the same
schema arrives at the same checks, from one call instead of four.

WHAT SURVIVED
-------------
The frozen doctrine (`prompts/council/doctrine.md`) — still the reviewer's only
knowledge, still no retrieval, still PRINCIPLE/model citations only. The planner
keeps its RAG: the corpus informs the draft, the doctrine judges it.

Stakeholder seats survive too, as an ADDITIVE opt-in (doctrine §7, the agency
sale). None configured — the default — is one agent and one call. Configure a
client's reviewer and it runs first; the Marketing Expert then treats it as one
more opinion to judge, never to average.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from app import config
from app.agents.runner import load_prompt, run_agent
from app.schemas import Feedback, Plan, SeatReview
from app.seats import BUILTIN_SEATS, SeatConfigError, SeatSpec
from app.seats import available as available_seats
from app.seats import resolve as resolve_seats
from app.validators import validate_seat_review

DOCTRINE_PROMPT = "council/doctrine"
REVIEWER_PROMPT = "council/marketing_expert"
REVIEWER_AGENT = "council.marketing_expert"

# Kept as a name other modules import. Empty by default: the reviewer is not a
# seat, and a campaign with no stakeholder seats has no seats at all.
SEATS = list(BUILTIN_SEATS)


def doctrine_version() -> str:
    """The version of the doctrine actually on disk this run."""
    _, version = load_prompt(DOCTRINE_PROMPT)
    return version


def seat_prompt_name(seat: str) -> str:
    return f"council/seat_{seat}"


def run_seat(
    seat: str,
    campaign_payload: dict[str, Any],
    plan: Plan,
    mock_seat: Optional[Callable[..., dict]] = None,
    spec: Optional[SeatSpec] = None,
) -> SeatReview:
    """One optional stakeholder seat — a client's own reviewer.

    No dispatcher and no tools: it judges from doctrine like the reviewer does.
    Validated here so a seat that breaks doctrine re-runs alone rather than
    poisoning the reviewer's input.
    """
    resolved = spec or available_seats().get(seat)
    if resolved is None:
        raise SeatConfigError(f"unknown council seat {seat!r}")

    review, _ = run_agent(
        agent=f"council.{seat}",
        prompt_name=seat_prompt_name(seat),
        model=config.FEEDBACK_MODEL,
        user_payload={**campaign_payload, "draft": plan.model_dump(mode="json"), "seat": seat},
        schema=SeatReview,
        preludes=[DOCTRINE_PROMPT],
        dispatcher=None,
        use_tools=False,
        validate=lambda r: validate_seat_review(r, resolved),
        mock_fn=(lambda p, d, _s=seat: mock_seat(p, d, _s)) if mock_seat else None,
    )
    review.doctrine_version = doctrine_version()
    return review


def run_council(
    campaign_payload: dict[str, Any],
    plan: Plan,
    validate_chair: Callable[[Feedback], Feedback],
    mock_seat: Optional[Callable[..., dict]] = None,
    mock_chair: Optional[Callable[..., dict]] = None,
    seat_reviews: Optional[list[SeatReview]] = None,
    seats: Optional[list[SeatSpec]] = None,
) -> tuple[Feedback, list[SeatReview]]:
    """The review pass: optional stakeholder seats, then the Marketing Expert.

    Returns (feedback, seat_reviews). With no stakeholder seats configured the
    list is empty and this is exactly one LLM call.

    `validate_chair` keeps its name because it is the same validator chain the
    chair used to run through — validate_feedback then validate_council.
    """
    roster = seats if seats is not None else resolve_seats()
    reviews: list[SeatReview] = list(seat_reviews) if seat_reviews else [
        run_seat(spec.slug, campaign_payload, plan, mock_seat) for spec in roster
    ]

    payload: dict[str, Any] = {
        **campaign_payload,
        "plan": plan.model_dump(mode="json"),
    }
    if reviews:
        payload["stakeholder_seats"] = [r.model_dump(mode="json") for r in reviews]

    feedback, _ = run_agent(
        agent=REVIEWER_AGENT,
        prompt_name=REVIEWER_PROMPT,
        model=config.FEEDBACK_MODEL,
        user_payload=payload,
        schema=Feedback,
        preludes=[DOCTRINE_PROMPT],
        dispatcher=None,
        use_tools=False,
        validate=validate_chair,
        mock_fn=mock_chair,
    )
    feedback.doctrine_version = doctrine_version()
    return feedback, reviews
