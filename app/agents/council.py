"""Addendum-03 evaluator — the Agent Council.

2-5 reviewer seats score BLIND (no planner ratings, no sight of each other),
then a chair consolidates into the single Feedback schema — final authority,
mandatory lens coverage, server-recomputed CCS, one refine loop. All v1 feedback
mechanics stay intact; only the reviewer topology changes.

v3 — THE COUNCIL NO LONGER RETRIEVES.
Every seat and the chair now judge from a frozen doctrine
(`prompts/council/doctrine.md`) instead of opening their own RAG slice. Three
reasons, in order of weight: a reviewer grounded in sample data has sample-grade
opinions; a creative director applies judgment rather than citing a document, so
forcing a citation produced either "no evidence in DB" (a broken-looking
reviewer) or citation theatre; and three retrieval fan-outs per pass — twice
when refine fires — were the bulk of a ~25 minute rumination.

The PLANNER keeps its retrieval. The corpus informs the draft; the doctrine
judges it. That division is the point, not a compromise.

TWO THINGS THAT LOOK OPTIONAL AND ARE NOT.
`use_tools=False` matters as much as `dispatcher=None`: run_agent attaches
TOOL_DEFS independently of the dispatcher, so passing None alone would leave the
seats calling tools that answer {"error": "no tools available"} — retrieval
removed in spirit, tool-loop turns burned in fact.
And `doctrine_version` is stamped HERE, after validation, never emitted by the
model: the audit question is which doctrine actually ran, not which one the model
believed it was reading.

Seats are prompt-configurable files (prompts/council/seat_*.md) — edit the file,
change the reviewer; add a file, add a reviewer. See app/seats.py.
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

# The default council. Kept as a module constant because the graph and the
# chair's deterministic ordering both need a canonical list; a campaign with
# stakeholder seats passes its own list instead.
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
    """ONE blind seat. Split out of run_council so the graph can fan the seats
    out concurrently.

    No dispatcher and no tools: this seat judges from doctrine. The blindness
    that used to be "its own retrieval slice" is now simply that it sees the
    draft and nothing else.

    Its output is validated HERE rather than at the chair, so a seat that breaks
    doctrine re-runs alone instead of failing the chair over an input the chair
    had no part in.
    """
    # available(), not resolve(): resolve() returns one campaign's roster, and a
    # stakeholder seat is absent from the default one. A seat with a prompt file
    # on disk has a well-defined spec regardless of who configured it.
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
    # Stamped, not asked for: the audit question is which doctrine ran, and only
    # the server knows which file it loaded.
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
    """Blind seats, then the chair merges. Returns (feedback, seat_reviews) —
    seat outputs are logged for the Activity/audit trail.

    `seat_reviews` lets a caller that has ALREADY run the seats (the graph, which
    runs them in parallel) hand them in rather than have them re-run here. The
    chair path below is identical either way.
    """
    roster = seats if seats is not None else resolve_seats()
    draft = plan.model_dump(mode="json")
    reviews: list[SeatReview] = list(seat_reviews) if seat_reviews else [
        run_seat(spec.slug, campaign_payload, plan, mock_seat) for spec in roster
    ]

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
        preludes=[DOCTRINE_PROMPT],
        dispatcher=None,
        use_tools=False,
        validate=validate_chair,
        mock_fn=mock_chair,
    )
    feedback.doctrine_version = doctrine_version()
    return feedback, reviews
