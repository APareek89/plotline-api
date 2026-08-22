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

### 2026-08-21 — Real plotline-rag wired in; two contract drifts caught

**Symptom:** Codex's plotline-rag landed with a contract that differs from devrag in two
ways: `/resolve_source_ids` returns `{resolved:[], unresolved:[]}` lists (devrag used a
`{id: bool}` dict), and it hosts ONLY the best_practice chunk corpus (no asset/stat/trend).
**Why 1:** devrag was written from the §7 contract sketch before the real schema existed.
**Deepest why type:** `knowledge-gap` (frozen contract wasn't frozen in wire-shape detail)
**Action taken:** RagClient normalizes both resolver shapes; RoutingRag fans out — chunk:*
→ plotline-rag (:8788, DynamoDB resolver), asset:/stat:/trend:* → devrag aux (:8787) until
plotline-kb lands. Citation validation is now two-layer: local subset (cited ⊆ retrieved
THIS run; regen runs seed the allowlist with the stored plan's validated ids) + remote
resolve, both fail closed. tier="seed" evidence rejected unless PLOTLINE_ALLOW_SEED_EVIDENCE.
**Preventive action:** When two repos share a "frozen" contract, freeze it as executable
fixtures (recorded request/response pairs), not prose — the normalizer in rag_client.py
now tolerates both shapes so the next drift degrades gracefully.

### 2026-08-21 — (inherited from Codex/rag) DynamoDB batch items were double-serialized

**Symptom:** Mocked DynamoDB store rejected `source_id_full` as a map instead of a string.
**Why:** Values were serialized once in code and again by the resource client's handlers —
`resource.meta.client` keeps resource-layer transforms; the batch path mixed API levels.
**Action taken (Codex):** Separate `session.client("dynamodb")` for typed batch writes;
Table resource only for ordinary gets. Lives in rag/src/plotline_rag/store/ddb.py.
**Preventive action:** Test every persistence adapter through moto at the SAME API level
and request shape production uses.
