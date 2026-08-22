"""Server-side validators (§3.9 hard validations + §11 guardrails).

The model never checks its own homework: CCS is recomputed here, every cited
source_id is resolved against the RAG service, and refine diffs are enforced.
One dead citation fails the whole output.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from app import ccs as ccs_mod
from app.rag_client import RoutingRag
from app.schemas import (
    Concept,
    CreatorContext,
    Evidence,
    Feedback,
    ObjectiveFamily,
    OptionsOutput,
    Plan,
)


# Addendum-01 §7.3 thresholds
SATURATION_MIN_ASSETS = 25   # below → saturation lens must say insufficient_data
NICHE_MIN_ASSETS = 30        # "supported niche" gates (else provisional at context time)
NICHE_MIN_CHUNKS = 15
THIN_PLAN_RATIO = 0.40       # >40% of concepts < 70 after refine → escalation artifact


class AgentValidationError(ValueError):
    """Raised when an agent output violates a hard validation. The message is
    fed back verbatim on the retry run."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _collect_evidence(items: Iterable[Evidence]) -> list[Evidence]:
    return [e for e in items]


def _resolvable_ids(evidence: list[Evidence]) -> list[str]:
    # PRINCIPLE with source_id "model" is the only citation that skips DB
    # resolution; a PRINCIPLE citing a chunk still resolves like everything else.
    return [e.source_id for e in evidence if e.source_id != "model"]


def resolve_or_fail(
    evidence: list[Evidence],
    rag: RoutingRag,
    errors: list[str],
    where: str,
    retrieved_ids: Optional[set[str]] = None,
) -> None:
    """Two independent layers, both mandatory (an id passing the DB check is
    NOT enough if it was never retrieved this run):

      1. local subset — every cited id ∈ ids surfaced by retrieval tools in
         THIS pipeline run (catches invented-but-real ids)
      2. remote existence — /resolve_source_ids against the RAG service
         (catches retrieved-then-rotted ids); unreachable resolver = reject
    """
    ids = sorted(set(_resolvable_ids(evidence)))
    if not ids:
        return
    if retrieved_ids is not None:
        invented = [i for i in ids if i not in retrieved_ids]
        if invented:
            errors.append(
                f"{where}: source_id(s) {invented} were NOT returned by any retrieval "
                "tool in this run — you may only cite ids your tools surfaced here; "
                'if evidence is insufficient, say so ("no evidence in DB", '
                "addressed=false / evidence_gap=true) instead of inventing a source"
            )
            ids = [i for i in ids if i in retrieved_ids]
            if not ids:
                return
    resolved = rag.resolve_source_ids(ids)
    dead = [i for i in ids if not resolved.get(i, False)]
    if dead:
        errors.append(
            f"{where}: unresolvable source_id(s) {dead} — every citation must be a "
            "DB-resolvable id returned by your retrieval tools in THIS run; if retrieval "
            'returned nothing, write "no evidence in DB" and set addressed=false or '
            "evidence_gap=true instead of inventing a source"
        )


# ---------------------- Addendum-01 §7.2: anti-generic mechanical checks ---
# Mechanical checks live HERE (prompts drift, validators don't). Judgment
# checks (R1, R4, R5, R7) are feedback-agent lenses; R6 activates with the
# trends corpus.

# R3 — banned-abstraction lexicon: hooks built from these are generic by
# construction. Lowercased substring match.
BANNED_ABSTRACTIONS = (
    "game-changer", "game changer", "boost productivity", "boost your productivity",
    "revolutionize", "next level", "take it to the next", "supercharge",
    "unlock the power", "unleash", "transform your workflow", "work smarter not harder",
    "you won't believe", "mind-blowing", "must-know", "level up your",
)

# R3 — specificity markers: a hook must carry ≥1 of {role, number, artifact,
# outcome}. Numbers are matched by regex; the rest by concrete-noun cues.
_SPECIFICITY_ROLE = ("editor", "founder", "developer", "designer", "marketer", "creator",
                     "freelancer", "student", "manager", "recruiter", "analyst", "pm")
_SPECIFICITY_ARTIFACT = ("invoice", "screenshot", "dashboard", "spreadsheet", "script",
                         "template", "prompt", "workflow", "scorecard", "bill", "subscription",
                         "clip", "video", "resume", "report", "timer", "checklist")
_SPECIFICITY_OUTCOME = ("saved", "saves", "cut", "cancelled", "replaced", "shipped",
                        "grew", "doubled", "fired", "wasted", "earned", "won", "lost", "caught")

# R2 — receipt cues: creative_direction must name a demonstrable artifact,
# not gesture at advice.
_RECEIPT_CUES = ("on camera", "on-screen", "screen record", "screen-rec", "screenshot",
                 "invoice", "split screen", "split-screen", "side-by-side", "timer",
                 "scorecard", "counter", "overlay", "live demo", "demos", "demo",
                 "before/after", "printed", "receipt", "recording", "dashboard", "workflow")

import re as _re

_NUMBER_RE = _re.compile(r"[\d₹$€%]")


def check_anti_generic(concept: Concept, errors: list[str]) -> None:
    """R2 + R3 mechanical layer. Judgment layers live in the feedback agent."""
    hook = concept.hook.verbal.lower()
    for phrase in BANNED_ABSTRACTIONS:
        if phrase in hook:
            errors.append(
                f"concept {concept.id}: R3 banned abstraction {phrase!r} in hook — replace "
                "with a concrete role/number/artifact/outcome"
            )
    has_specific = bool(_NUMBER_RE.search(hook)) or any(
        w in hook for w in _SPECIFICITY_ROLE + _SPECIFICITY_ARTIFACT + _SPECIFICITY_OUTCOME
    )
    if not has_specific:
        errors.append(
            f"concept {concept.id}: R3 specificity floor — hook must contain at least one of "
            "a role, a number, a named artifact, or a concrete outcome"
        )
    direction = concept.creative_direction.lower()
    if not any(cue in direction for cue in _RECEIPT_CUES):
        errors.append(
            f"concept {concept.id}: R2 receipt required — creative_direction must name a "
            "demonstrable artifact (what the viewer literally SEES as proof)"
        )


# ------------------------------------------------------------------- intake --


def validate_intake(context: CreatorContext, *, require_addendum_fields: bool = True) -> CreatorContext:
    errors: list[str] = []
    if len(context.clarifying_questions) > 3:
        errors.append("max 3 clarifying_questions, only if truly blocking")
    if require_addendum_fields:
        # Addendum-01 §7.1 — R1 and format feasibility depend on these.
        if not context.audience_sophistication:
            errors.append("audience_sophistication is required (novice/practitioner/expert)")
        if not context.tool_access:
            errors.append("tool_access is required (what can the creator actually demo)")
        if not context.positioning_depth:
            errors.append("positioning_depth is required (beginner_guide/power_user)")
    if errors:
        raise AgentValidationError(errors)
    return context


# --------------------------------------------------------------------- plan --


def validate_plan(
    plan: Plan,
    context: CreatorContext,
    rag: RoutingRag,
    *,
    previous_plan: Optional[Plan] = None,
    flagged_concept_ids: Optional[set[str]] = None,
    retrieved_ids: Optional[set[str]] = None,
) -> Plan:
    errors: list[str] = []
    family = ccs_mod.WEIGHTS.keys()  # noqa: F841  (families validated below)

    fam = _family_of(context)
    required_elements = set(ccs_mod.applicable_elements(fam))

    expected_slots = context.cadence.slots
    if len(plan.concepts) != expected_slots:
        errors.append(
            f"plan has {len(plan.concepts)} concepts but cadence requires {expected_slots} slots"
        )

    seen_ids: set[str] = set()
    for concept in plan.concepts:
        if concept.id in seen_ids:
            errors.append(f"duplicate concept id {concept.id}")
        seen_ids.add(concept.id)

        scored = {s.element for s in concept.element_scores}
        missing = required_elements - scored
        if missing:
            errors.append(
                f"concept {concept.id}: missing element_scores for {sorted(missing)} — every "
                "applicable element must be present (addressed=false with why_not for honest gaps)"
            )
        extra = scored - required_elements
        if extra:
            errors.append(
                f"concept {concept.id}: elements {sorted(extra)} are not applicable for "
                f"objective family {fam.value}"
            )
        evidence = _collect_evidence(e for s in concept.element_scores for e in s.evidence)
        # Subset check applies to concepts authored THIS run: all of them on a
        # fresh plan, only flagged ones on a refine (untouched concepts are
        # byte-identical and their citations were checked when first written).
        authored_now = previous_plan is None or concept.id in (flagged_concept_ids or set())
        if authored_now:
            check_anti_generic(concept, errors)  # Addendum-01 R2/R3 mechanical layer
        resolve_or_fail(
            evidence, rag, errors, f"concept {concept.id}",
            retrieved_ids=retrieved_ids if authored_now else None,
        )

    # Refine pass: may touch only flagged concepts — untouched must be byte-identical.
    if previous_plan is not None:
        flagged = flagged_concept_ids or set()
        prev_by_id = {c.id: c for c in previous_plan.concepts}
        for concept in plan.concepts:
            if concept.id in flagged:
                continue
            prev = prev_by_id.get(concept.id)
            if prev is None:
                errors.append(f"refine pass introduced new concept {concept.id} — not allowed")
                continue
            if _canonical(concept) != _canonical(prev):
                errors.append(
                    f"refine pass modified unflagged concept {concept.id} — untouched concepts "
                    "must be byte-identical (diff-checked)"
                )

    if errors:
        raise AgentValidationError(errors)
    return plan


def _canonical(concept: Concept) -> str:
    return json.dumps(concept.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _family_of(context: CreatorContext) -> ObjectiveFamily:
    from app.schemas import objective_family

    return objective_family(context.objective)


# ------------------------------------------ Addendum-01 §7.1: format stage --


def validate_formats(
    formats: "FormatOptions",
    rag: RoutingRag,
    retrieved_ids: Optional[set[str]] = None,
) -> "FormatOptions":
    from app.schemas import FormatOptions  # noqa: F401  (typing only)

    errors: list[str] = []
    seen: set[str] = set()
    for option in formats.options:
        if option.format_id in seen:
            errors.append(f"duplicate format_id {option.format_id}")
        seen.add(option.format_id)
        resolve_or_fail(
            list(option.evidence), rag, errors, f"format {option.format_id}",
            retrieved_ids=retrieved_ids,
        )
    if errors:
        raise AgentValidationError(errors)
    return formats


# ----------------------------------------------------------------- feedback --


def validate_feedback(
    feedback: Feedback,
    plan: Plan,
    context: CreatorContext,
    rag: RoutingRag,
    retrieved_ids: Optional[set[str]] = None,
    niche_asset_count: Optional[int] = None,
) -> Feedback:
    errors: list[str] = []
    fam = _family_of(context)
    required_elements = set(ccs_mod.applicable_elements(fam))
    plan_ids = {c.id for c in plan.concepts}
    verdict_ids = {v.concept_id for v in feedback.concept_verdicts}

    missing_concepts = plan_ids - verdict_ids
    if missing_concepts:
        errors.append(f"feedback missing verdicts for concepts {sorted(missing_concepts)} — verdict on ALL concepts required")
    unknown = verdict_ids - plan_ids
    if unknown:
        errors.append(f"feedback references unknown concept ids {sorted(unknown)}")

    scores_by_concept = {c.id: {s.element: s for s in c.element_scores} for c in plan.concepts}

    for verdict in feedback.concept_verdicts:
        verdict_elements = {ev.element for ev in verdict.element_verdicts}
        missing = required_elements - verdict_elements
        if missing:
            errors.append(
                f"concept {verdict.concept_id}: element_verdicts missing {sorted(missing)} — "
                "verdict on EVERY element is mandatory (any missing = invalid)"
            )

        # saturation lens must cite the DB (or explicitly have no similar assets)
        sat = verdict.lenses.saturation
        if sat.similar_count > 0 and not sat.source_id and not sat.insufficient_data:
            errors.append(
                f"concept {verdict.concept_id}: saturation lens with similar_count>0 must cite a source_id"
            )
        # Addendum-01 §7.3 saturation guard: below the niche asset threshold the
        # lens must declare insufficient_data — a rating there is fake confidence.
        if (
            niche_asset_count is not None
            and niche_asset_count < SATURATION_MIN_ASSETS
            and not sat.insufficient_data
        ):
            errors.append(
                f"concept {verdict.concept_id}: niche has only {niche_asset_count} assets "
                f"(< {SATURATION_MIN_ASSETS}) — saturation lens must set insufficient_data=true, "
                "never a rating"
            )

        planned = scores_by_concept.get(verdict.concept_id, {})
        for ev in verdict.element_verdicts:
            planned_score = planned.get(ev.element)
            if ev.final_rating is None and ev.verdict != "agree":
                errors.append(
                    f"concept {verdict.concept_id}/{ev.element}: non-agree verdict requires final_rating"
                )
            if (
                ev.final_rating is None
                and ev.verdict == "agree"
                and planned_score is not None
                and planned_score.addressed
            ):
                errors.append(
                    f"concept {verdict.concept_id}/{ev.element}: agree on an addressed element "
                    "must set final_rating (= the proposed rating)"
                )

        evidence = _collect_evidence(
            e for ev in verdict.element_verdicts for e in ev.evidence
        )
        if sat.source_id:
            evidence = evidence + [
                Evidence(tag="REF", source_id=sat.source_id, claim="saturation check", as_of=None)
            ]
        resolve_or_fail(
            evidence, rag, errors, f"feedback for concept {verdict.concept_id}",
            retrieved_ids=retrieved_ids,
        )

        # Server recomputes CCS — model arithmetic is advisory only.
        server_ccs = ccs_mod.compute_ccs(fam, ccs_mod.final_ratings_of(verdict))
        if server_ccs != verdict.ccs_final:
            # not a retry-error: silently repairing is banned, so we surface the
            # recompute by overriding and logging (caller logs the discrepancy).
            verdict.ccs_final = server_ccs

    if errors:
        raise AgentValidationError(errors)
    return feedback


# ------------------------------------------------------------------ options --


def validate_options(
    options: OptionsOutput,
    qualified_concept_ids: set[str],
) -> OptionsOutput:
    errors: list[str] = []
    got = {co.concept_id for co in options.concept_options}
    missing = qualified_concept_ids - got
    if missing:
        errors.append(f"options missing for qualified concepts {sorted(missing)}")
    extra = got - qualified_concept_ids
    if extra:
        errors.append(
            f"options produced for non-qualified concepts {sorted(extra)} — options are only "
            "generated for concepts with ccs_final > 70 and no kill flags"
        )
    if errors:
        raise AgentValidationError(errors)
    return options
