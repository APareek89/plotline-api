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

## 2026-08-24 — A truncated LLM response reported itself as a JSON syntax bug

**Symptom.** `council.chair` burned all 3 attempts (868s) with
`Expecting ',' delimiter: line 217 column 295 (char 18136)` and once
`no JSON object found in agent output`. The previous session's handoff predicted a
prompt-size/schema bug and pre-authorized sharding the chair per concept.

**Why-chain.**
1. Why did the chair fail? Its output was not parseable JSON.
2. Why was it unparseable? It stopped mid-string — the body was a *prefix* of valid JSON.
3. Why did it stop mid-string? `stop_reason` came back `max_tokens`.
4. Why did it hit the cap? Extended-thinking tokens are billed against the SAME
   `max_tokens` budget as the answer; a thinking chair + a large Feedback object
   exceeded 16000 together.
5. Why did nobody see that? `runner._llm_call` never inspected `stop_reason` — it
   returned the truncated text, and `json.loads` blamed the only thing it can see:
   syntax. **Deepest why: `code`** — a missing guard on an external-API status field
   turned an overflow into a lie about its own cause.

**Action (targeted at why-5, not why-1).** Raise `AgentTruncated` on
`stop_reason=max_tokens` instead of parsing; join all text blocks (was first-only);
`MAX_OUTPUT_TOKENS` 16000→32000; stream always (the SDK refuses non-streamed calls whose
budget implies >10min — raising the cap alone broke the planner until streaming landed);
persist the failing body to `data/runs/raw/`.

**Preventive action for the class.** Never hand an external API's payload to a parser
without first checking the status field that says whether the payload is complete. The
parser's error message describes the *shape of what arrived*, never *why it arrived that
way* — so an unchecked truncation/pagination/partial-write always surfaces as a plausible
lie. Two acceptance checks now hold this: a truncated body is never parsed as complete,
and a multi-block answer is never silently halved.

**Cost of the missing observability.** Three full real-mode runs (~45 min, planner + 3
seats each) to learn what one saved raw body showed instantly. The very next bug (R2
lexicon) was diagnosed in a single run *because* the raw capture existed.

## 2026-08-24 — A validator checked a lexicon while the prompt described an intent

**Symptom.** `campaign_planner.options` failed R2 on all 3 attempts: "receipt required —
the storyline must name what the viewer literally SEES as proof".

**Why-chain.** The planner HAD written visible proof — "camera holds on the screen as
the bloom visibly shrinks". Why did it still fail? R2 is a literal substring match against
`validators._RECEIPT_CUES`. Why didn't the planner use those words? The prompt only said
"R1-R7 anti-generic rules apply: receipts, specificity". **Deepest why: `design`** — one
rule had two representations (prose in the prompt, a lexicon in the validator) and nothing
kept them in sync. Same species as the two bugs fixed the session before.

**Action.** The prompt now has the lexicon *injected* from `validators._RECEIPT_CUES` via
`prompt_replacements`, plus an explicit "a camera move is not a receipt" contrast. The
validator was NOT weakened. planner 1.2.0→1.3.0.

**Preventive action.** When a check is mechanical, the prompt must quote the mechanism,
generated from the same constant the check reads — never a hand-copy. A test asserts every
enforced cue appears in the built system prompt, so drift fails in CI, not in a paid run.
