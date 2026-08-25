<!-- prompt: canon_plan | version: 1.0.0 -->
CANON PLAN — name the cast, products and places this board actually needs.

You are given the approved shot board. Every `cast_refs`, `product_refs` and
`env_refs` id on that board must resolve to a sheet you plan here, and you must
NOT plan a sheet the board never references. Canon is expensive and reusable;
inventing an unused one spends the user's money on nothing.

FOR A PRODUCT, name the GEOMETRY RISK. Product accuracy is harder than face
consistency and packaging text is the hardest part of it. `risk_notes` lists the
features that mutate between generations — hinges, threads, lock holes, opening
direction, attachment points, pattern density, engraved marks. A product with a
non-empty risk_notes needs extra three-quarter views before it can be trusted,
so only list what genuinely mutates, and list all of it.

`locks` are what must NOT change between shots: base wardrobe, hair,
distinguishing marks for a character; proportions, label text, finish for a
product; light direction, time of day, fixed props for an environment.

RIGHTS. If a sheet depicts a real identifiable person, voice or trademark, set
`rights` to "consented" only when the user has recorded consent — otherwise say
"unverified" and it will be refused. If nobody real is depicted, "fictional" is
the correct and honest answer.

You are planning sheets, not generating them. `asset_ids` stays empty and
`coverage` stays empty; the server generates the required views and fills both.

OUTPUT SHAPE — bare JSON, exact keys, no envelope:
{"sheets":[{"id":"@priya","kind":"character|product|environment|voice",
            "label":"…","brief":"…","asset_ids":[],"coverage":{},
            "locks":[],"slot_cost":1,"risk_notes":[],
            "rights":"owned|consented|fictional|unverified","consent_ref":null,
            "native_review":{},"version":1}]}
