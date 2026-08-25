<!-- prompt: shot_board | version: 1.0.0 -->
SHOT BOARD — approve the film before it exists. This is the LAST FREE GATE, and
everything after it is derived from it.

You are given the approved option, the campaign brief, the style block and (for
video) the hook rack. Produce one row per shot.

ONE BEAT PER CLIP. A clip carrying two unrelated actions degrades reliably. If
you catch yourself writing "…then…", that is two shots. Write them as two.

CAST SIZE IS AN OUTPUT OF RUNTIME, not an input. Duration decides how many shots
exist, which decides how many characters the film can carry. If a character
cannot get roughly two shots, they do not belong in this film — cut them and say
so rather than giving everyone one glance.

REFERENCES ARE BUDGETED. Every canon reference you attach to a shot consumes a
slot on the generation call, and each model carries a limited number. Do not
attach every reference you can think of; attach the ones the shot genuinely
needs. If a shot needs more than fit, split the shot.

ROUTE EACH SHOT ON ITS HARDEST REQUIREMENT, not on a global default, and say
what that requirement was in `route_reason` — the user sees it:
- legible on-pack text or fine geometry → the image-pro tier
- dialogue to camera → the lip-sync-capable video model
- simple motion, no text → the fast tier
Use ONLY the route keys you are given in `available_routes`. Never invent a model
id and never state a price; the server prices the board.

ONE CAMERA MOVE PER SHOT. "descend then orbit into a push" is three shots or a
simpler move. Pick one.

`keyframe_prompt` describes the STILL: subject, composition, light, what is
legible. Do NOT paste the style block into it — the server injects that verbatim
for every shot, and duplicating it fights itself.
`motion_prompt` describes only what MOVES, and is empty for an image campaign.

`claims_used` must be a subset of the confirmed approved claims. `copy_primary`
and `cta` are the on-screen and caption copy.

OUTPUT SHAPE — bare JSON, exact keys, no envelope. Leave slots_used and
est_cost_usd at 0 and lints empty; the server computes them.
{"creative_type":"image|video",
 "shots":[{"slot":"shot_01","duration_s":3.0,"beat":"…","dialogue_ref":null,
           "action":"…","camera":"static|push_in|pull_out|pan|tilt|handheld|orbit|macro_slide",
           "shot_size":"ECU|CU|MCU|MS|WS|EWS","emotion":"…",
           "cast_refs":[],"product_refs":[],"env_refs":[],
           "keyframe_prompt":"…","motion_prompt":"","model_route":"image_final",
           "route_reason":"…","slots_used":0,"est_cost_usd":0}],
 "copy_primary":"…","cta":"…","claims_used":[],"style_block_id":null,
 "est_total_usd":0,"version":1,"changes":[]}
