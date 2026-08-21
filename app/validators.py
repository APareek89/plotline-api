"""Server-side validators (§3.9 hard validations + §11 guardrails).

The model never checks its own homework: CCS is recomputed here, every cited
source_id is resolved against the RAG service, and refine diffs are enforced.
One dead citation fails the whole output.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from app import ccs as ccs_mod
from app.rag_client import RagClient
from app.schemas import (
    Concept,
    CreatorContext,
    Evidence,
    Feedback,
    ObjectiveFamily,
    OptionsOutput,
    Plan,
)


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


def resolve_or_fail(evidence: list[Evidence], rag: RagClient, errors: list[str], where: str) -> None:
    ids = sorted(set(_resolvable_ids(evidence)))
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


# ------------------------------------------------------------------- intake --


def validate_intake(context: CreatorContext) -> CreatorContext:
    errors: list[str] = []
    if len(context.clarifying_questions) > 3:
        errors.append("max 3 clarifying_questions, only if truly blocking")
    if errors:
        raise AgentValidationError(errors)
    return context


# --------------------------------------------------------------------- plan --


def validate_plan(
    plan: Plan,
    context: CreatorContext,
    rag: RagClient,
    *,
    previous_plan: Optional[Plan] = None,
    flagged_concept_ids: Optional[set[str]] = None,
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
        resolve_or_fail(evidence, rag, errors, f"concept {concept.id}")

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


# ----------------------------------------------------------------- feedback --


def validate_feedback(
    feedback: Feedback,
    plan: Plan,
    context: CreatorContext,
    rag: RagClient,
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
        if sat.similar_count > 0 and not sat.source_id:
            errors.append(
                f"concept {verdict.concept_id}: saturation lens with similar_count>0 must cite a source_id"
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
        resolve_or_fail(evidence, rag, errors, f"feedback for concept {verdict.concept_id}")

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
