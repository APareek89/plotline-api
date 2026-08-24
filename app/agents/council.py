"""Addendum-03 evaluator — the Agent Council.

2-3 RAG-grounded reviewer seats score BLIND (no planner ratings, no sight of
each other; each grounded only in its own retrieval slice), then a chair
consolidates into the single Feedback schema — final authority, mandatory
lens coverage, server-recomputed CCS, one refine loop. All v1 feedback
mechanics stay intact; only the reviewer topology changes.

Seats are prompt-configurable files (prompts/council/seat_*.md) — edit the
file, change the reviewer. Optional stakeholder seats ("My CMO") land later
by dropping in another file.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from app import config
from app.agents.runner import run_agent
from app.schemas import CreatorContext, Feedback, Plan, SeatReview

SEATS = ["performance", "brand", "platform"]


def run_council(
    campaign_payload: dict[str, Any],
    plan: Plan,
    dispatcher_factory: Callable[[], Any],
    validate_chair: Callable[[Feedback], Feedback],
    mock_seat: Optional[Callable[..., dict]] = None,
    mock_chair: Optional[Callable[..., dict]] = None,
) -> tuple[Feedback, list[SeatReview]]:
    """Blind seats in sequence (each gets a FRESH dispatcher = its own
    retrieval slice), then the chair merges. Returns (feedback, seat_reviews)
    — seat outputs are logged for the Activity/audit trail."""
    draft = plan.model_dump(mode="json")
    reviews: list[SeatReview] = []
    for seat in SEATS:
        review, _ = run_agent(
            agent=f"council.{seat}",
            prompt_name=f"council/seat_{seat}",
            model=config.FEEDBACK_MODEL,
            user_payload={**campaign_payload, "draft": draft, "seat": seat},
            schema=SeatReview,
            dispatcher=dispatcher_factory(),
            validate=None,
            mock_fn=(lambda p, d, _s=seat: mock_seat(p, d, _s)) if mock_seat else None,
        )
        reviews.append(review)

    feedback, _ = run_agent(
        agent="council.chair",
        prompt_name="council/chair",
        model=config.FEEDBACK_MODEL,
        user_payload={
            **campaign_payload,
            "plan": draft,
            "seats": [r.model_dump(mode="json") for r in reviews],
        },
        schema=Feedback,
        dispatcher=dispatcher_factory(),
        validate=validate_chair,
        mock_fn=mock_chair,
    )
    return feedback, reviews
