"""Addendum-01 §05: server-side parsing of typed commands into action events.

Both input paths — button tap and typed text — normalize to the same
(artifact_id, event) signal and hit the same handler. This parser is
deterministic (works identically in MOCK_LLM mode); free text that isn't a
recognized command falls through to the conversational agent.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# c3 / c03 / concept 3 → canonical c03
_CONCEPT_REF = re.compile(r"\b(?:concept\s*)?c?(\d{1,2})\b", re.IGNORECASE)
_FORMAT_REF = re.compile(r"\bf(\d)\b", re.IGNORECASE)
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1}

_APPROVE = re.compile(r"^\s*(approve|accept|ship|lgtm|yes to)\b", re.IGNORECASE)
_REGEN = re.compile(r"^\s*(regenerate|regen|redo|rework|rewrite|retry)\b", re.IGNORECASE)
_FEEDBACK = re.compile(r"^\s*(feedback|change|tweak|fix)\b", re.IGNORECASE)
_PICK = re.compile(r"^\s*(pick|choose|use|go with|select)\b", re.IGNORECASE)
_PLAN_WORD = re.compile(r"\b(plan|all of it|everything)\b", re.IGNORECASE)
_THIS = re.compile(r"\b(this|it|that one)\b", re.IGNORECASE)


def _concept_ids(text: str, batch_order: Optional[list[str]] = None) -> list[str]:
    ids = [f"c{int(m):02d}" for m in _CONCEPT_REF.findall(text)]
    if not ids and batch_order:
        for word, pos in _ORDINALS.items():
            if re.search(rf"\b{word}\b(?:\s+one)?", text, re.IGNORECASE):
                try:
                    ids.append(batch_order[pos - 1 if pos > 0 else -1])
                except IndexError:
                    pass
    return ids


def parse_command(
    text: str,
    *,
    panel_focus: Optional[str] = None,
    batch_order: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    """Returns {event, targets, note} or None (not a command → conversational).

    events: approve_concept | approve_plan | regenerate_concept | pick_format
    """
    stripped = text.strip()
    if not stripped:
        return None

    # "this / it" with an open panel resolves to the focused artifact (§02)
    def _targets() -> list[str]:
        ids = _concept_ids(stripped, batch_order)
        if not ids and panel_focus and _THIS.search(stripped):
            return [panel_focus]
        return ids

    if _PICK.match(stripped):
        formats = [f"f{m}" for m in _FORMAT_REF.findall(stripped)]
        if formats:
            return {"event": "pick_format", "targets": formats, "note": None}

    if _APPROVE.match(stripped):
        if _PLAN_WORD.search(stripped) and not _CONCEPT_REF.search(stripped):
            return {"event": "approve_plan", "targets": [], "note": None}
        targets = _targets()
        if targets:
            return {"event": "approve_concept", "targets": targets, "note": None}
        return None

    if _REGEN.match(stripped) or _FEEDBACK.match(stripped):
        targets = _targets()
        if not targets:
            return None
        # everything after the reference (or a comma/dash) is the user's note
        note = None
        m = re.search(r"[,—-]\s*(.+)$", stripped)
        if m:
            note = m.group(1).strip()
        return {"event": "regenerate_concept", "targets": targets, "note": note}

    return None
