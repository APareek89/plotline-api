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

## 2026-08-25 — A network blip threw away ten minutes of paid, successful work

**Symptom.** The final real-mode verification on the production models died with
`httpx.RemoteProtocolError: peer closed connection without sending complete message body`.
The thread escalated. In the run log the chair had NO ROW AT ALL — it simply was not there.

**Why-chain.**
1. Why did the rumination fail? The chair raised an httpx transport error.
2. Why did that kill the run? `run_agent`'s retry loop catches
   `(ValidationError, AgentValidationError, ValueError, json.JSONDecodeError)`.
   `httpx.RemoteProtocolError` is none of those, so it propagated out of run_agent.
3. Why was there no log row? `_persist_log` runs on success and on final validation
   failure — both INSIDE the loop. An exception escaping the loop skips it, so the node
   vanished from observability exactly when observability was needed.
4. Why was the cost so high? The planner (209s) and all three seats (120s/137s/139s)
   had already SUCCEEDED and been paid for. One blip at the join discarded all of it.
5. **Deepest why: `code`** — the retry loop was built around "the model gave a bad
   answer" and had no concept of "the model never got to answer". Those need opposite
   handling: one re-prompts with a correction, the other re-issues verbatim.

**Action (aimed at why-5).** `AgentTransport(ValueError)` — subclassing ValueError so the
existing loop picks it up, the same trick `AgentTruncated` uses. `_llm_call` catches
`httpx.TransportError` and `anthropic.APIConnectionError` ONLY: an `APIStatusError`
(401/403/400) is a real answer from the server and must surface immediately rather than
spin. A `last_transport` flag suppresses the "your previous output failed validation"
message, because there was no output — appending it would make the model rewrite a good
answer it never sent. Small backoff between attempts; still bounded by
MAX_VALIDATION_RETRIES. Two acceptance checks pin both halves.

**What this does NOT fix.** The retry re-runs ONE node. The real exposure is that a
failure anywhere still discards every completed node in the rumination — this run had
four successful paid nodes riding on the fifth. That is the stage-level checkpoint work
(Handoff "VIDEO FLOW" item 5), and v3 makes it worse by adding five stages. This bug is
the cheap half; the structural half is still open.

**Preventive action for the class.** A retry loop around a network call needs to
distinguish "bad answer" from "no answer". Check what the loop's `except` clause does NOT
name — anything unlisted escapes past the persistence that makes the failure diagnosable,
so the most expensive failures become the least visible ones.

## 2026-08-25 — A gate with a renderer nobody routed to

**Symptom.** Every v3 acceptance test passed and two API-driven QA runs passed,
but in the browser the campaign brief drew a dashed placeholder with the type
name and NO BUTTONS. The flow dead-ended at the first new gate.

**Why-chain.**
1. Why no fields? `CampaignBriefCard` never rendered.
2. Why not? The thread page routes an artifact to the campaign renderer only if
   `isCampaignArtifact(type)` is true; otherwise it falls to the legacy card.
3. Why was it false? `CAMPAIGN_ARTIFACT_TYPES` — a SECOND list, separate from
   the switch — did not contain the new types.
4. Why did nothing catch it? Every test drove the API. The renderer, the switch
   and the allowlist all live in TypeScript, and the suite is Python; the two
   lists that had to agree were never compared by anything.
5. **Deepest why: `design`** — one fact ("this type is a campaign artifact") had
   two representations in the same file, and adding a case to the switch looked
   complete because the switch is where a reader goes to add a renderer.

**Action (aimed at why-5).** A guard in the Python suite that READS the
TypeScript: every `case` in the switch must appear in the allowlist, and every
v3 type the server can emit must appear in both. Verified it bites by removing
one entry and watching it fail with that entry named.

**Preventive action for the class.** This is the third instance of the same
species — the R2 lexicon (prompt vs validator), the W1 table and B3 slot caps
(prompt vs config), and now the renderer allowlist (switch vs list). When one
fact has two representations, something has to compare them, and the comparison
belongs wherever the test suite actually runs even if that crosses a language
boundary. "Both files are in the repo" is not a check.

**Second lesson.** Two green API QA runs and 155 passing tests did not notice a
dead end at the FIRST gate a user reaches. An API test proves the server can
produce an artifact; it says nothing about whether the artifact is reachable.
Any flow with a UI needs at least one pass driven through the UI — the same
lesson as "the tests call save_block(), they never type", one layer up.

## 2026-08-26 — Three cards, one button, two of them unreachable

**Symptom.** Driving the new two-pane UI, the options turn offered a single
`Approve` above the composer. Three campaign options were on screen; only
option THREE could ever be approved, and the button did not say so.

**Why-chain.**
1. Why one button? `_options_from()` was handed `artifacts[-1]`.
2. Why the last one? The CTA lift was written against turns that post ONE
   actioned card, which is every turn except this one.
3. Why did the acceptance check miss it? It compared the EVENT NAMES a card
   declares against the event names offered, as sets. All three cards declare
   `approve`, so `{"approve"} ⊆ {"approve"}` held while two of the three cards
   had no reachable button.
4. **Deepest why: `design`** — the check compared verbs and ignored objects.
   An action's identity is (what it does, what it does it TO); dropping the
   object made three different CTAs look like one.

**Action (aimed at why-4).** The lift covers every actioned artifact in the
turn, and labels carry the artifact id when more than one card is in play —
three buttons reading `Approve` cannot say what they approve. The guard now
compares `(artifact_id, event)` PAIRS, and a second test asserts each option is
separately approvable and that no two approve labels are identical.

**Preventive action for the class.** When a check compares two representations
of an action, the comparison key must include the target, not just the verb.
A set of verbs is the smallest thing that looks like a valid check and isn't:
it passes exactly when the objects differ, which is the case worth catching.

## 2026-08-26 — The board refused honestly and the real fault was two stages back

**Symptom.** `shot_board` burned all 3 attempts. Its first reply was literally
`{"error": "INCOMPLETE INPUT — …resubmit"}`; the next returned zero shots. The
run escalated with "The shot board kept failing its lints."

**Why-chain.**
1. Why did the board fail? Its input was `{"brief": {}, "option": null}`.
2. Why was it empty? The in-memory workspace was lost — the API had been
   restarted mid-campaign.
3. Why didn't `_rehydrate()` restore it? It handles `campaign_option`,
   `campaign_detail`, `model_confirm`, `creative_set` and `ad_card` — the v2
   artifacts. It was never taught `campaign_brief`, `hook_rack`, `canon_sheet`
   or `keyframe_board`.
4. Why not? v3 added five stages and their artifacts; the rehydrator lives in a
   different part of the file and nothing forced the two to agree.
5. **Deepest why: `design`** — the same one-fact-two-representations family.
   The set of artifacts the workspace DEPENDS ON grew, and the code that
   restores them from the transcript did not.

**Action (aimed at why-5).** The four v3 artifacts are rehydrated. A test kills
every in-memory workspace mid-flow and asserts the brief, the hook rack and the
approved option all survive. Verified it bites: without the fix it fails with
`brief = None`, which is exactly what the live run hit.

**Preventive action for the class.** An agent that refuses because its INPUT is
incomplete is reporting a fault upstream of itself, and the retry loop will
faithfully repeat the bad input three times and then blame the last node. When
a node fails on validation, read what it was GIVEN before reading what it
returned — `node_input` in the run log answers that in one look, and it is the
reason this took one run to find rather than three.

**Second lesson.** Both of today's bugs were invisible to 165 passing tests and
visible within minutes of clicking through the app. That is now the fourth
time. The rule stands: a flow with a UI needs one pass driven through the UI,
every time, no exceptions.

## 2026-08-26 — The board was blamed for an input nobody gave it

**Symptom.** A live QA run escalated: "The shot board kept failing its lints." Asked
for a two-shot film, it produced eight shots and six distinct characters, failed B1/B4
three times, and gave up.

**Why-chain.**
1. Why did the lints fail? Too many shots, too many characters, too many refs per shot.
2. Why did it over-produce? It was reconstructing the campaign from the brief alone.
3. Why only the brief? Its payload carried `option: ws.get("approved_option_json")`.
4. Why was that null? **`approved_option_json` was READ by two turns and WRITTEN by
   nothing.** It never existed. The script and board had been handed `option: null`
   for the entire life of v3.
5. **Deepest why: `design`** — a mirrored copy of a fact that nobody kept in sync,
   which is the same family as the R2 lexicon and the renderer allowlist. The cheapest
   version of that lesson is not to keep a second copy at all.

**Action (aimed at why-5).** `_approved_option_json(ws)` DERIVES the JSON from the one
slot that is actually written. There is no second slot to drift. A test asserts the
option reaches the board and that no mirrored key comes back.

**Preventive action for the class.** A slot that is only ever read is not a slot, it is
a typo with a default. Grep any new workspace key for its WRITE site before trusting it;
`ws.get(x)` returning None looks identical whether x is empty or misspelled.

**Second lesson — read what a node was GIVEN.** The run log's `node_input` said
`option: null` immediately. The validation errors, which is where attention goes, only
described the *shape of the output*. An agent that fails its checks is often correct
about the input it was handed.
