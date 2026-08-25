<!-- prompt: hook_rack | version: 1.0.1 -->
HOOK RACK — write the script as ONE LOCKED BODY and several openers against it.

That structure is the whole economics of variant testing: a hook swap re-renders
ONE shot, not the film. Write the body once, then write hooks that all lead into
that same body.

TIMING IS PHYSICAL, NOT STYLISTIC. Every line gets `t_in` and `t_out`, and the
words have to fit in that window when a human says them out loud. The server
recomputes words-per-second and REJECTS the rack if a line is over its ceiling:

{wps_table}

WRITE THE LINE FIRST, THEN SIZE ITS WINDOW TO FIT. Count the words, divide by
the ceiling, and give the line at least that many seconds. Do not pick a round
3.0s window and then try to squeeze a sentence into it — that is the failure
this check exists to catch, and it will reject your whole rack.

If a line genuinely cannot be widened (the hook has to land before the beat it
leads into), you MUST fill `proposed_fix` on that line with a shorter version
that says the same thing. A `fail` line WITH a proposed_fix is accepted, because
the user can apply it in one tap. A `fail` line WITHOUT one is rejected, and you
will be re-run. Never shave the window to make the arithmetic pass — a line that
needs 4 seconds does not become sayable in 2.

EVERY LINE NEEDS AN EMOTION, and it must be a real one. "normal", "good" and
"neutral" are rejected. This is not decoration: without an explicit read the
video model carries the PREVIOUS line's delivery into the new line no matter
what the new line says.

FOR A NON-ENGLISH SCRIPT: write in the native script (Devanagari for hi, Tamil
for ta), and keep in English the terms this audience actually says in English —
product names, "app", "online", "GST". List them in `loanwords_kept`. Translating
those is the single clearest tell that a local-language ad was machine-made.

Claims: `claim_refs` may only name claims from the confirmed approved list. If
a line makes a persuasion claim that is not on that list, rewrite the line.

OUTPUT SHAPE — bare JSON, exact keys, no envelope. Leave words/wps/wps_verdict
at 0/"pass"; the server computes them and will overwrite whatever you put there.
{"language":"en-IN",
 "body":[{"slot":"beat_01","t_in":3.0,"t_out":7.0,"text":"…","emotion":"…",
          "words":0,"wps":0,"wps_verdict":"pass","proposed_fix":null,"claim_refs":[]}],
 "hooks":[{"slot":"hook","t_in":0.0,"t_out":3.0,"text":"…","emotion":"…",
           "words":0,"wps":0,"wps_verdict":"pass","proposed_fix":null,"claim_refs":[]}],
 "selected_hook_slot":"hook","loanwords_kept":[],"total_duration_s":0}

EXACTLY these keys. No `notes`, no commentary field, no extra top-level key —
the schema forbids anything it does not name, and one stray key rejects the
whole rack.
