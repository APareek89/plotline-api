"""The rumination pipeline as an explicit LangGraph StateGraph.

WHY THIS EXISTS
---------------
`app/campaign.py` drove rumination as straight-line Python: plan, then three
seats in a for-loop, then the chair, then an `if flagged:` refine block. That
works, but it hides the two things that are actually interesting about the
flow — that the seats are INDEPENDENT (they can run concurrently, and their
blindness is a structural fact, not a convention) and that the refine loop is a
CONDITIONAL EDGE with a hard cap of one pass.

Modelling it as a graph makes both machine-checkable instead of
convention-checkable:

  * the three seats are three nodes fanned out from one edge, so "blind" means
    "no edge carries another seat's output", not "we remembered not to pass it"
  * the refine cap is `refine_done` in the state and a conditional edge that
    can only route to refine once — not an `if` a future edit could loosen
  * every node returns a Pydantic-validated slice of state, so an invalid
    intermediate cannot travel down an edge

WHAT IT DELIBERATELY DOES NOT CHANGE
------------------------------------
The agents, prompts, validators, CCS recomputation and citation checks are
untouched — this module calls the SAME `run_agent`, the SAME
`validate_feedback`, the SAME `_dispatcher`. A graph that quietly changed the
rules would make the 19 acceptance checks meaningless. Rumination is the piece
being ported first because it is the piece with genuine graph shape; the linear
stages (name -> paths -> cards, and detail -> generate -> creative) are a state
machine over user events, and a StateGraph would add ceremony without adding
truth.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Callable, Optional

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.agents.council import SEATS
from app.schemas import (
    CampaignContext,
    CampaignOptions,
    CreatorContext,
    Feedback,
    Plan,
    SeatReview,
)


def _keep_last(a: Any, b: Any) -> Any:
    """Reducer for single-writer slots. The fan-out means several nodes commit
    concurrently; anything not explicitly accumulated takes the newer value."""
    return b if b is not None else a


class RuminationState(BaseModel):
    """Graph state. Pydantic, so an invalid value cannot cross an edge — the
    schema check that used to live only at the run_agent boundary now also
    guards every hand-off between nodes."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ---- inputs, fixed for the whole run
    context: CampaignContext
    shadow: CreatorContext
    niche_assets: int = 0
    note: Optional[str] = None
    regenerate_ids: list[str] = Field(default_factory=list)
    previous: Optional[CampaignOptions] = None

    # ---- accumulated. `retrieved` is the union of every id any node has
    # legitimately pulled this run; the citation validator reads it, so it must
    # accumulate across the parallel seats rather than be overwritten by
    # whichever seat finishes last.
    retrieved: Annotated[list[str], operator.add] = Field(default_factory=list)
    seat_reviews: Annotated[list[SeatReview], operator.add] = Field(default_factory=list)

    # ---- produced
    options: Annotated[Optional[CampaignOptions], _keep_last] = None
    plan: Annotated[Optional[Plan], _keep_last] = None
    feedback: Annotated[Optional[Feedback], _keep_last] = None
    flagged: Annotated[list[str], _keep_last] = Field(default_factory=list)

    # ---- the one-pass cap, as state rather than as an `if`
    refine_done: Annotated[bool, _keep_last] = False


class RuminationDeps(BaseModel):
    """Everything the nodes need from campaign.py, injected rather than
    imported, so this module never reaches back into the driver (and the tests
    can drive the graph with fakes)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_options: Callable[..., CampaignOptions]
    run_council: Callable[..., tuple[Feedback, list[SeatReview]]]
    build_plan: Callable[[CampaignContext, CampaignOptions, CreatorContext], Plan]
    flagged_ids: Callable[[Any, Feedback], set[str]]
    merge_feedback: Callable[[Feedback, Feedback], Feedback]
    objective_family: Callable[[Any], Any]
    seat_runner: Optional[Callable[..., SeatReview]] = None
    on_step: Optional[Callable[[str], None]] = None


def build_rumination_graph(deps: RuminationDeps):
    """plan -> (3 blind seats in parallel) -> chair -> [refine once] -> END."""

    def _step(label: str) -> None:
        if deps.on_step:
            deps.on_step(label)

    # ------------------------------------------------------------------ plan
    def plan_options(state: RuminationState) -> dict[str, Any]:
        _step("drafting options")
        retrieved = set(state.retrieved)
        options = deps.run_options(
            state.context, state.shadow, retrieved, state.niche_assets,
            note=state.note, previous=state.previous,
            flagged=set(state.regenerate_ids or []),
        )
        plan = deps.build_plan(state.context, options, state.shadow)
        # run_options mutates `retrieved` in place as tools resolve ids; commit
        # only what is NEW so the accumulating reducer stays correct.
        return {
            "options": options,
            "plan": plan,
            "retrieved": sorted(retrieved - set(state.retrieved)),
        }

    # ------------------------------------------------- seats (parallel fan-out)
    def _seat_node(seat: str):
        def run_seat(state: RuminationState) -> dict[str, Any]:
            _step("council review")
            assert state.options is not None and state.plan is not None
            seen = set(state.retrieved)
            review, surfaced = deps.seat_runner(
                seat=seat,
                context=state.context,
                shadow=state.shadow,
                options=state.options,
                plan=state.plan,
                retrieved=seen,
                niche_assets=state.niche_assets,
            )
            # NOTE what is absent: no other seat's review is read here, and
            # nothing this node returns is visible to a sibling — they commit
            # to the same reducer only after all three have finished.
            #
            # What IS shared: the ids this seat legitimately surfaced. In the
            # sequential version all three seats mutated ONE `retrieved` set and
            # the chair's citation validator read it, so a chair citing a seat's
            # source stayed legal. Running the seats in parallel would silently
            # narrow that unless each commits its ids back here.
            return {"seat_reviews": [review],
                    "retrieved": sorted(surfaced - set(state.retrieved))}

        run_seat.__name__ = f"seat_{seat}"
        return run_seat

    # ------------------------------------------------------------------ chair
    def chair(state: RuminationState) -> dict[str, Any]:
        _step("council review")
        assert state.options is not None and state.plan is not None
        feedback, reviews = deps.run_council(
            state.context, state.shadow, state.options, state.plan,
            set(state.retrieved), state.niche_assets,
            seat_reviews=state.seat_reviews or None,
        )
        family = deps.objective_family(state.shadow.objective)
        flagged = sorted(deps.flagged_ids(family, feedback))
        extra = [r for r in reviews if r not in state.seat_reviews]
        return {"feedback": feedback, "flagged": flagged,
                "seat_reviews": extra}

    # ----------------------------------------------------------------- refine
    def refine(state: RuminationState) -> dict[str, Any]:
        _step(f"refining {len(state.flagged)} option(s) — one pass, diff-checked")
        assert state.options is not None and state.feedback is not None
        flagged = set(state.flagged)
        fixes = [f.change for v in state.feedback.concept_verdicts
                 if v.concept_id in flagged for f in v.fixes]
        retrieved = set(state.retrieved)
        options = deps.run_options(
            state.context, state.shadow, retrieved, state.niche_assets,
            note=state.note, previous=state.options, flagged=flagged, fixes=fixes,
        )
        plan = deps.build_plan(state.context, options, state.shadow)
        subset = Plan(series=plan.series,
                      concepts=[c for c in plan.concepts if c.id in flagged],
                      changes=plan.changes)
        refined, refined_reviews = deps.run_council(
            state.context, state.shadow, options, subset,
            retrieved, state.niche_assets, only_ids=flagged,
        )
        merged = deps.merge_feedback(state.feedback, refined)
        return {
            "options": options,
            "plan": plan,
            "feedback": merged,
            "seat_reviews": refined_reviews,
            "retrieved": sorted(retrieved - set(state.retrieved)),
            "refine_done": True,          # the cap, recorded in state
        }

    def should_refine(state: RuminationState) -> str:
        # ONE pass, enforced by the edge: once refine_done is set there is no
        # route back, so a future edit cannot accidentally make this a loop.
        if state.flagged and not state.refine_done:
            return "refine"
        return END

    graph = StateGraph(RuminationState)
    graph.add_node("plan_options", plan_options)
    for seat in SEATS:
        graph.add_node(f"seat_{seat}", _seat_node(seat))
    graph.add_node("chair", chair)
    graph.add_node("refine", refine)

    graph.add_edge(START, "plan_options")
    # one edge out to three nodes = concurrent fan-out; they join at the chair
    for seat in SEATS:
        graph.add_edge("plan_options", f"seat_{seat}")
        graph.add_edge(f"seat_{seat}", "chair")
    graph.add_conditional_edges("chair", should_refine, {"refine": "refine", END: END})
    graph.add_edge("refine", END)
    return graph.compile()
