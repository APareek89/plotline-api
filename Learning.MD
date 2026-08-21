# Learning.MD — Plotline
> AGENT: add an entry whenever the basic flow changes, the user reports a bug or gives
> corrective feedback, or something took real time to figure out. CHECK THIS FILE FIRST
> before debugging — the answer may already be here.

### 2026-08-21 — CCS band test failed on hand-picked ratings

**Symptom:** test_status_bands asserted a rating combo lands between 70 and 85; it scored 87.
**Why 1:** Downgrading three 10–15-weight elements only drops ~13 CCS points from 100.
**Why 2:** The weight table concentrates 55% of weight in hook+audience+retention, so mid-band requires downgrading a heavy element (hook M drops 8.3 alone).
**Deepest why type:** `knowledge-gap`
**Action taken:** Picked hook M + diff M + retention M (82) for the qualified band. (Why 1 level.)
**Preventive action:** When writing threshold tests against weighted scores, compute the score first — don't eyeball rating combos.
