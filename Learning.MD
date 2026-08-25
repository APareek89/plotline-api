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

## 2026-08-25 — An optional field that was impossible to skip

**Symptom.** "Make policy extract optional." The Brand Policy Document was ALREADY
optional in `BrandBlock` and `_policy_text` handled its absence gracefully, so the
request looked like it was already satisfied.

**Why-chain.**
1. Why does it feel mandatory? Because the Brand card cannot be completed without it.
2. Why not? `claims_confirmed` gates the card, and the only way to set it is the
   "Confirm claims" button.
3. Why can't that button be reached? It rendered only when
   `aux.candidates.approved_claims.length > 0 || banned_words.length > 0`.
4. Why would that ever be zero? No policy doc + a product description with nothing
   quotable extracts nothing — confirmed live: `{approved_claims: [], banned_words: []}`.
5. **Deepest why: `design`** — an optional INPUT was wired to a mandatory OUTPUT. The
   schema said optional; the only path through the UI said otherwise.

**Action (aimed at why-5).** Show the confirm surface whenever extraction has run, allow
manual entry, and permit confirming an empty list with the consequence stated inline.

**Preventive action for the class.** When a field is optional, trace the whole path it
feeds and check the terminal action is still reachable when it is absent. "Optional" is a
property of the PATH, not of the field declaration — a nullable column proves nothing.
Two acceptance checks now pin it: the card completes with no policy doc, and an empty
approved list still rejects any claim a detail tries to use.

**Second lesson, cheaper.** The instinct was to read "make it optional" as "relax the
gate". The gate was not the problem, and relaxing `claims_confirmed` would have broken a
stated invariant. Reading the request as a bug report rather than a spec change found a
fix that made compliance stricter, not looser.

## 2026-08-25 — "Works on my machine" three times in one session

**Symptom.** Three separate breakages, all with the same signature: green locally, broken
where it actually runs.
1. `langgraph` imported by `app/graph.py` but never added to `requirements.txt` → 83 tests
   passed while the Render build produced an app that crashed on import.
2. `tests/conftest.py` isolated `DB_PATH` but not `LOG_DIR` → every test run appended to the
   real `agent_runs.jsonl`, and the new observability page showed a deliberate test fixture
   (`campaign_planner.test` returning `{"options":[{"option_id":"o1"}]}`) as a production
   failure. The user reported it as an app bug. It was mine.
3. `npm run build` and `npm run dev` share `.next` → verifying a change with a production
   build left the running dev server serving MODULE_NOT_FOUND. Cost three restarts.

**Why-chain.** Why did each pass locally? Because the local environment had something the
target didn't — an extra installed package, a shared writable path, a warm build directory.
Why wasn't that caught? Because "the tests pass" was treated as equivalent to "this works".
**Deepest why: `process`** — verification ran in the developer environment, which is by
definition the one environment where the bug cannot appear.

**Action.** (1) `langgraph>=1.2,<2` pinned, and proven by importing `app.main` from a
**fresh venv built from requirements.txt alone**. (2) `LOG_DIR` monkeypatched to `tmp_path`,
with a test asserting `config.LOG_DIR != data/runs`, and the 76 polluted records removed.
(3) `npm run typecheck` (`tsc --noEmit`) added so compile checks never touch the directory
dev is serving.

**Preventive action for the class.** A green test run in the dev venv proves the code is
correct, not that it is *deployable*. Anything that changes dependencies, writes to a shared
path, or produces build artefacts needs one check in a clean environment before "done" —
and the check should be constructed so that the old behaviour would have failed it.

## 2026-08-25 — Three bugs stacked behind one refused message

**Symptom.** User typed a real brief with an attached image at the path step and got
"Didn't catch a campaign command."

**Why-chain.** (1) `_parse` at `stage=paths` only accepted path-choice keywords, so a real
brief fell through to the hint — the app asked the user to pick a path *after* they had
answered by typing. (2) Fixing that exposed the attachment being stringified into the prose
as `[attached images: upl_x]`, so the agent got an opaque id as words with no instruction —
the file was silently dropped. (3) Fixing that exposed intake hard-failing 3/3 because the
model emitted a HALF product block (`name: null, description: null`) when the message named
a use case but not a product. **Deepest why: `design`** — each layer assumed the layer
before it had produced something well-formed, and none of them said so out loud.

**Action.** Free text at `paths` starts the conversational path with that message; uploads
travel as `UserEvent.upload_ids` and are applied SERVER-SIDE (`_apply_pending_uploads`) so a
model that ignores them cannot lose the user's file; prompt 1.3.0 states blocks are
all-or-nothing with the user's own sentence as the worked example.

**Preventive action.** The tests passed throughout, because they call `save_block()` with
structured data — they never type like a person. Any conversational surface needs at least
one test that goes in through the same door a user does. Bugs 1–3 were all reachable only by
driving the app.

## 2026-08-25 — A validator and a doctrine gave the same seat opposite orders

**Symptom.** With the frozen council doctrine wired up, `test_unmapped_claim_produces_a_kill_flag`
failed: the whole campaign hard-failed after 3 chair attempts with
`a multiplier claim ('3x faster')`. The council had correctly caught an unapproved
claim and was rejected for saying so.

**Why-chain.**
1. Why did the chair fail? `validate_council`'s benchmark guard matched "3x faster".
2. Why was that text there? The Brand seat put it in `kill_recommendation`, and the
   chair relayed it into `lenses.claims_safety`.
3. Why did the seat write a number? Because doctrine D8 ORDERS it to: "quote the
   offending phrase verbatim when you flag it."
4. Why did the guard exist? Because a council with no corpus cannot know a benchmark,
   so a magnitude in its output is a fabrication.
5. **Deepest why: `design`** — both rules were right, and neither knew about the other.
   The guard could not distinguish a magnitude the council ASSERTS from one it QUOTES
   in order to refuse. They are opposites, and the one place they collide is the
   compliance path — the highest-value thing the council does.

**Action (aimed at why-5).** `_check_no_benchmarks` strips paired-quote spans before
scanning. A number held at arm's length is attributed to the draft; only what is left
is the council speaking in its own voice. The guard was NOT weakened for anything
unquoted, and the patterns stayed narrow (percentages, ad-metric acronyms, duration
thresholds, multipliers) so a seat can still reference the draft's own numbers —
"shot 2 runs 4 seconds" is the feasibility judgment D10 asks for.

**Preventive action for the class.** When a mechanical check and a prompt instruction
touch the same field, ask what the check does to the output the instruction REQUIRES.
The R2 lexicon bug was prompt-and-validator drift — two representations of one rule.
This is its mirror: two rules, each correct, whose overlap nobody owned. A guard on
model output needs to know the difference between the model's own voice and text it
is quoting, or it will punish the model hardest exactly when it is most right.

## 2026-08-25 — A cached graph pinned every campaign's progress to the first thread

**Symptom.** Found while making the rumination graph seat-aware, not from a report.
`_RUMINATION_GRAPH` was a module-level singleton built on first use, and its
`on_step` was `lambda label: _working.__setitem__(thread_id, label)` — closing over
the `thread_id` of whichever campaign happened to build it.

**Why.** Every later campaign in the same process wrote its progress labels onto the
FIRST campaign's thread. "Labelled working steps, never a bare spinner" therefore held
on campaign one and broke silently on campaign two — the second user would sit on a
bare spinner while a stale thread flickered. A server restart between campaigns hides
it, which is why local testing never surfaced it.
**Deepest why: `code`** — a closure captured a per-request value into a
process-lifetime object.

**Action.** `on_step` reads `runner.current_thread` (a contextvar already set per
worker thread at the top of every turn) instead of a closure. Graphs are now cached
per seat-roster, so the cache itself grew a legitimate key and the bug's hiding place
went with it. An acceptance check drives `on_step` under a different contextvar value.

**Preventive action.** Anything cached for the process lifetime must not close over a
per-request value. When memoising a factory, check every callable in its arguments for
captured request state — the cache key hides the bug, because the second caller gets
an object built for the first.
