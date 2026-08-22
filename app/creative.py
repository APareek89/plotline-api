"""Addendum-02: Creative Studio thread driver — one conversation from concept
to Post Card.

Sequence (§02): origin pin + confidence card → route → script (per-shot edit)
→ voice → editable asset prompts w/ cost → generate (per-asset accept/reroll,
seam QA with one free auto re-roll) → end-card → Post Card → offer next
concept. Inherits the Addendum-01 envelope, numbering, and dual input paths.

§08 rules enforced here, not in prompts: user-edited prompts used verbatim;
no generation without a visible cost + explicit UserEvent; per-asset
re-rolls only; post_card assembled only when every required asset accepted.
"""
from __future__ import annotations

import re
import threading
import time
import traceback
from typing import Any, Optional

from app import config, store
from app.agents import creative_mock as director
from app.fal_client import MediaError, estimate_cost, generate
from app.schemas import ArtifactEnvelope, PostCard, ScriptPackage, UserEvent
from app.thread import _actions, _say, _working

CREDIT_USD = 0.10


def _asset_url(asset_id: str) -> str:
    return f"/api/assets/{asset_id}/file"


def _pending(thread_id: str) -> dict[str, Any]:
    """Driver state riding on the thread row is too small for this — keep an
    in-memory workspace per creative thread (single-process dev; the durable
    record is messages + assets + generation_log)."""
    return _WORKSPACES.setdefault(thread_id, {
        "route": None, "script": None, "voice": None,
        "prompts": {}, "assets": {}, "accepted": set(),
        "endcards": [], "endcard": None, "spent": 0.0, "draft_only": False,
        "seam_rerolled": set(),
    })


_WORKSPACES: dict[str, dict[str, Any]] = {}


# ------------------------------------------------------------------- start --


def start_creative_thread(series_id: str, concept_id: str, option_id: str = "A") -> dict[str, Any]:
    series = store.get_series(series_id)
    bundle = store.get_plan(series_id)
    if not series or not bundle:
        raise ValueError("series/plan not found")
    concept = next((c for c in bundle["plan"]["concepts"] if c["id"] == concept_id), None)
    if concept is None:
        raise ValueError(f"concept {concept_id} not found")
    context = series["context"]

    # §01: text-only concepts never enter Creative Studio — Post Card emitted
    # directly with copy only.
    if context.get("content_type") == "text":
        card = _copy_only_card(series_id, concept, context)
        return {"post_card": card, "thread": None}

    opts = next((o for o in (bundle["options"] or {}).get("concept_options", [])
                 if o["concept_id"] == concept_id), None)
    option = next((o for o in (opts or {}).get("options", []) if o["option_id"] == option_id), {})

    thread = store.create_thread(series_id, kind="creative")
    store.set_production_status(series_id, concept_id, "in_production")
    t = threading.Thread(target=_opening_turn,
                         args=(series_id, thread["id"], concept, option, option_id), daemon=True)
    t.start()
    return {"thread": thread, "post_card": None}


def _opening_turn(series_id: str, thread_id: str, concept: dict, option: dict, option_id: str) -> None:
    try:
        _working[thread_id] = "reading the concept"
        ws = _pending(thread_id)
        ws["concept"], ws["option"], ws["option_id"] = concept, option, option_id
        ws["series_id"] = series_id

        states = {s["concept_id"]: s for s in store.get_concept_states(series_id)}
        state = states.get(concept["id"], {})
        rec = director.route_recommendation(concept)
        script_probe = director.build_script(concept, option, rec["route"])
        est_credits = script_probe["cost_estimate_credits"]

        origin = ArtifactEnvelope(
            type="concept", id=concept["id"], title=concept["title"],
            payload={"concept": concept, "option": option, "option_id": option_id,
                     "ccs": state.get("ccs"), "coverage": state.get("coverage"),
                     "status": state.get("status"), "origin_pin": True,
                     "verdict": None, "options": None, "provisional": False},
            actions=[],
        )
        confidence = ArtifactEnvelope(
            type="confidence_card", id="confidence", title=f"Confidence — CCS {state.get('ccs')}",
            payload={
                "ccs": state.get("ccs"), "coverage": state.get("coverage"),
                "beats": director.beats_of(concept, option),
                "route_rec": rec,
                "cost_estimate_credits": est_credits,
                "cost_estimate_usd": round(est_credits * CREDIT_USD, 2),
                "draft_first_offer": True,  # §09 open call: offered, not forced
            },
            actions=_actions(("approve_route", "Approve route", "primary"),
                             ("change_route", "Change route", "secondary")),
        )
        _say(thread_id,
             f"Landed with {concept['id']}, option {option_id}. Confidence card before any script.",
             [origin, confidence],
             question=f"Route recommendation: {rec['route'].replace('_', ' ')} — approve, or change it?")
        store.set_thread_stage(thread_id, "route")
        store.log_artifact_activity(thread_id, "confidence", "proposed", f"route rec {rec['route']}")
    except Exception as exc:
        _fail(thread_id, f"Couldn't open the studio thread: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


# ------------------------------------------------------------------ events --

_SHOT_RE = re.compile(r"\b(?:shot|frame|slide)[ _]?(\d+)\b", re.IGNORECASE)
_END_RE = re.compile(r"\bend[- ]?card ?(\d)\b", re.IGNORECASE)


def handle_event(event: UserEvent) -> None:
    thread = store.get_thread(event.thread_id)
    if not thread:
        raise ValueError("thread not found")
    store.append_message(event.thread_id, "user", event.model_dump(mode="json"))
    stage = thread["stage"]

    if event.type == "action":
        _dispatch(thread, stage, event.action.event, event.action.artifact_id, None)
        return

    text = (event.text or "").strip()
    low = text.lower()
    # typed path — §02: identical events to taps
    if stage == "route":
        if "one take" in low or "one-take" in low:
            _dispatch(thread, stage, "route_one_take", "confidence", None)
        elif "interpolat" in low:
            _dispatch(thread, stage, "route_interpolated", "confidence", None)
        elif "approve" in low or "keyframe" in low:
            _dispatch(thread, stage, "approve_route", "confidence", None)
        else:
            _hint(thread["id"], 'Approve the route, or say "one take" / "interpolated" / "change route".')
    elif stage == "script":
        m = _SHOT_RE.search(low)
        if "approve" in low and not m:
            _dispatch(thread, stage, "approve_script", "script", None)
        elif m:
            _dispatch(thread, stage, "edit_shot", "script", {"shot": int(m.group(1)), "note": text})
        else:
            _hint(thread["id"], 'Approve the script, or name a shot: "tighten shot 2 VO — punchier".')
    elif stage == "voice":
        if "a" in low.split() or "asha" in low:
            _dispatch(thread, stage, "pick_voice_a", "voices", None)
        elif "b" in low.split() or "neel" in low:
            _dispatch(thread, stage, "pick_voice_b", "voices", None)
        else:
            _hint(thread["id"], 'Pick a voice: "Voice A" or "Voice B" (previews on the card).')
    elif stage == "prompts":
        if "draft" in low:
            _dispatch(thread, stage, "draft_first", "prompts", None)
        elif "generate" in low or "all" in low:
            _dispatch(thread, stage, "generate_all", "prompts", None)
        else:
            _hint(thread["id"], '"Generate all", or "draft shot 1 only" to spend less first.')
    elif stage in ("review_assets", "endcard"):
        m = _SHOT_RE.search(low)
        e = _END_RE.search(low)
        if ("re-roll" in low or "reroll" in low or "redo" in low) and m:
            _dispatch(thread, stage, f"reroll_shot_{int(m.group(1)):02d}", "assets", text)
        elif "accept" in low:
            _dispatch(thread, stage, "accept_all", "assets", None)
        elif e or "end card" in low or "endcard" in low or "end-card" in low:
            n = int(e.group(1)) if e else (int(re.search(r"\d", low).group()) if re.search(r"\d", low) else 1)
            _dispatch(thread, stage, f"pick_end_{n}", "endcards", None)
        else:
            _hint(thread["id"], 'Pick an end-card ("end-card 2"), "re-roll shot 3", or "accept".')
    elif stage == "done":
        if "posted" in low or "mark" in low:
            _dispatch(thread, stage, "mark_posted", "postcard", None)
        elif "start" in low or "next" in low:
            _dispatch(thread, stage, "start_next", "postcard", None)
        else:
            _hint(thread["id"], 'The Post Card is ready — "Mark posted", download it, or "start next".')
    else:
        _hint(thread["id"], "Still working — one moment.")


def _hint(thread_id: str, text: str) -> None:
    _say(thread_id, "Didn't catch a studio command.", question=text)


def _dispatch(thread: dict, stage: str, event: str, artifact_id: str, extra: Any) -> None:
    thread_id, series_id = thread["id"], thread["series_id"]
    ws = _pending(thread_id)

    if stage == "route" and event in ("approve_route", "route_one_take", "route_interpolated", "change_route"):
        if event == "change_route":
            _say(thread_id, "Three routes, same script skeleton.",
                 [ArtifactEnvelope(type="confidence_card", id="routes", title="Pick a route",
                                   payload={"route_rec": director.route_recommendation(ws["concept"]),
                                            "choices": ["keyframe_cuts", "one_take", "interpolated"]},
                                   actions=_actions(("approve_route", "Keyframe cuts", "primary"),
                                                    ("route_one_take", "One take", "secondary"),
                                                    ("route_interpolated", "Interpolated", "secondary")))])
            return
        route = {"approve_route": None, "route_one_take": "one_take", "route_interpolated": "interpolated"}[event] \
            or director.route_recommendation(ws["concept"])["route"]
        ws["route"] = route
        store.log_artifact_activity(thread_id, "confidence", "approved", f"route {route}")
        threading.Thread(target=_script_turn, args=(thread_id,), daemon=True).start()
        return

    if stage == "script":
        if event == "approve_script":
            store.log_artifact_activity(thread_id, "script", "approved")
            threading.Thread(target=_voice_turn, args=(thread_id,), daemon=True).start()
            return
        if event == "edit_shot" and isinstance(extra, dict):
            _edit_shot(thread_id, extra["shot"], extra["note"])
            return

    if stage == "voice" and event.startswith("pick_voice"):
        ws["voice"] = "voice_a" if event.endswith("_a") else "voice_b"
        store.log_artifact_activity(thread_id, "voices", "approved", ws["voice"])
        threading.Thread(target=_prompts_turn, args=(thread_id,), daemon=True).start()
        return

    if stage == "prompts" and event in ("generate_all", "draft_first"):
        ws["draft_only"] = event == "draft_first"
        threading.Thread(target=_generate_turn, args=(thread_id,), daemon=True).start()
        return

    if stage in ("review_assets", "endcard"):
        if event.startswith("reroll_"):
            slot = event.removeprefix("reroll_")
            threading.Thread(target=_reroll_asset, args=(thread_id, slot, extra), daemon=True).start()
            return
        if event.startswith("accept"):
            for slot in list(ws["assets"].keys()):
                ws["accepted"].add(slot)
            _say(thread_id, "All assets accepted.")
            if ws.get("endcard") is not None:
                threading.Thread(target=_assemble_turn, args=(thread_id,), daemon=True).start()
            return
        if event.startswith("pick_end_"):
            idx = int(event.rsplit("_", 1)[1]) - 1
            ws["endcard"] = ws["endcards"][idx] if 0 <= idx < len(ws["endcards"]) else ws["endcards"][0]
            store.log_artifact_activity(thread_id, "endcards", "approved", ws["endcard"])
            for slot in list(ws["assets"].keys()):
                ws["accepted"].add(slot)  # picking the end-card closes review (§02)
            threading.Thread(target=_assemble_turn, args=(thread_id,), daemon=True).start()
            return

    if stage == "done":
        if event == "mark_posted":
            card = store.get_post_card(ws.get("card_id", ""))
            if card:
                card["status"] = "posted"
                card["posted_at"] = time.time()
                store.save_post_card(card)
                store.set_production_status(series_id, ws["concept"]["id"], "posted")
                _say(thread_id, "Marked posted. I'll nudge for results in 72h — pasted numbers feed the next plan.")
            return
        if event == "start_next":
            nxt = _next_planned_concept(series_id)
            if nxt:
                res = start_creative_thread(series_id, nxt, "A")
                ordinal = res["thread"]["ordinal"] if res.get("thread") else "—"
                _say(thread_id, f"Thread — {ordinal:02d} opened for {nxt}." if isinstance(ordinal, int)
                     else f"{nxt} produced.")
            else:
                _say(thread_id, "No planned concepts left in this series.")
            return

    _say(thread_id, f"Nothing changed — {event!r} doesn't apply right now.")


# ------------------------------------------------------------------- turns --


def _script_turn(thread_id: str) -> None:
    try:
        _working[thread_id] = "writing the script package"
        ws = _pending(thread_id)
        script = director.build_script(ws["concept"], ws["option"], ws["route"])
        ScriptPackage.model_validate(script)  # v1 §04 contract enforced
        ws["script"] = script
        _say(thread_id, "Script package — VO + 4 shots, locks travel verbatim.",
             [ArtifactEnvelope(type="script_package", id="script", title="Script package",
                               payload=script,
                               actions=_actions(("approve_script", "Approve script", "primary")))],
             question='Approve, or name a shot to edit ("tighten shot 2 VO — punchier").')
        store.set_thread_stage(thread_id, "script")
        store.log_artifact_activity(thread_id, "script", "proposed", f"route {ws['route']}")
    except Exception as exc:
        _fail(thread_id, f"Script generation failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _edit_shot(thread_id: str, shot_no: int, note: str) -> None:
    ws = _pending(thread_id)
    script = ws.get("script")
    sid = f"shot_{shot_no:02d}"
    shot = next((s for s in (script or {}).get("shots", []) if s["shot_id"] == sid), None)
    if not shot:
        _say(thread_id, f"No {sid} in this script.")
        return
    old = shot["vo_segment"]
    shot["vo_segment"] = director.edit_shot_vo(shot, note)
    script["vo_script"] = " ".join(s["vo_segment"] for s in script["shots"] if s["vo_segment"])
    store.log_artifact_activity(thread_id, "script", "refined", f"{sid} VO edited (user: {note[:80]})")
    # §02 step 5: only the named shot changes; the diff is shown
    _say(thread_id, f"{sid} updated — only {sid}.",
         [ArtifactEnvelope(type="script_package", id="script", title="Script package (updated)",
                           payload={**script, "diff": {sid: {"was": old, "now": shot["vo_segment"]}}},
                           actions=_actions(("approve_script", "Approve script", "primary")))])


def _voice_turn(thread_id: str) -> None:
    try:
        _working[thread_id] = "preparing voice previews"
        ws = _pending(thread_id)
        voices = director.voice_options()
        for v in voices["voices"]:  # 5s draft-tier previews, cost logged
            preview = generate("audio", "This is how your reel will sound. Fast, clear, receipts on screen.",
                               tier="draft", voice=v["id"])
            aid = store.add_asset(thread_id, f"preview_{v['id']}", "audio", preview["path"],
                                  {"model": preview["model"], "tier": "draft"}, preview["cost"])
            store.log_generation(thread_id, aid, "generate", model=preview["model"], cost=preview["cost"])
            ws["spent"] += preview["cost"]
            v["preview_url"] = _asset_url(aid)
        _say(thread_id, "Pick a voice — 5-second previews attached.",
             [ArtifactEnvelope(type="voice_options", id="voices", title="Voice options",
                               payload={**voices, "note": "Draft previews = Kokoro; final render = MiniMax HD"},
                               actions=_actions(("pick_voice_a", "Voice A", "primary"),
                                                ("pick_voice_b", "Voice B", "secondary")))],
             question="A or B?")
        store.set_thread_stage(thread_id, "voice")
        store.log_artifact_activity(thread_id, "voices", "proposed")
    except MediaError as exc:
        _fail(thread_id, f"Voice previews failed: {exc}", "")
    finally:
        _working.pop(thread_id, None)


def _prompts_turn(thread_id: str) -> None:
    ws = _pending(thread_id)
    script = ws["script"]
    artifacts = []
    for shot in script["shots"]:
        slot = shot["shot_id"].replace("shot", "frame")
        prompt = director.frame_prompt(shot, "9:16")
        ws["prompts"][slot] = prompt
        artifacts.append(ArtifactEnvelope(
            type="asset_prompt", id=f"prompt_{slot}", title=f"{slot} · keyframe prompt",
            payload={"asset_slot": slot, "model": config.MEDIA_MODELS["image_final"],
                     "prompt_text": prompt, "cost": estimate_cost("image", tier="final"),
                     "ratio": "9:16", "locks": director.LOCKS},
            actions=[],
        ))
    total = (sum(estimate_cost("image", tier="final") for _ in script["shots"])
             + estimate_cost("video", duration_s=sum(s["duration_s"] for s in script["shots"]))
             + estimate_cost("audio", chars=len(script["vo_script"]), tier="final"))
    gate = ArtifactEnvelope(
        type="asset_prompt", id="generate_gate", title=f"Generate — est ${total:.2f} ({total / CREDIT_USD:.0f} credits)",
        payload={"asset_slot": "all", "model": "batch", "prompt_text": "", "cost": round(total, 2),
                 "ratio": "9:16", "locks": [],
                 "note": "Draft-first renders shot 1 only — see the look before the full spend."},
        actions=_actions(("generate_all", "Generate all 4", "primary"),
                         ("draft_first", "Draft shot 1 only", "secondary")),
    )
    _say(thread_id, "Keyframe prompts — editable before generate, cost per frame shown.",
         artifacts + [gate],
         question="Generate all, or draft shot 1 first?")
    store.set_thread_stage(thread_id, "prompts")
    for a in artifacts:
        store.log_artifact_activity(thread_id, a.id, "proposed")


def _generate_turn(thread_id: str) -> None:
    try:
        ws = _pending(thread_id)
        script, shots = ws["script"], ws["script"]["shots"]
        todo = shots[:1] if ws["draft_only"] else shots
        items = []
        for shot in todo:
            slot = shot["shot_id"]
            frame_slot = slot.replace("shot", "frame")
            _working[thread_id] = f"rendering {frame_slot}"
            prompt = ws["prompts"].get(frame_slot) or director.frame_prompt(shot, "9:16")
            frame = generate("image", prompt, tier="final", seed=hash(slot) % 10_000)
            fid = store.add_asset(thread_id, frame_slot, "image", frame["path"],
                                  {"model": frame["model"], "prompt": prompt}, frame["cost"])
            store.log_generation(thread_id, fid, "generate", prompt=prompt, model=frame["model"],
                                 seed=str(frame.get("seed")), cost=frame["cost"])
            ws["spent"] += frame["cost"]

            _working[thread_id] = f"animating {slot}"
            clip = generate("video", shot["motion_prompt"], duration_s=shot["duration_s"],
                            image_url=frame.get("url"))
            cid = store.add_asset(thread_id, slot, "video", clip["path"],
                                  {"model": clip["model"], "prompt": shot["motion_prompt"],
                                   "frame_asset": fid}, clip["cost"])
            store.log_generation(thread_id, cid, "generate", prompt=shot["motion_prompt"],
                                 model=clip["model"], cost=clip["cost"])
            ws["spent"] += clip["cost"]
            ws["assets"][slot] = cid
            items.append({"asset_id": cid, "slot": slot, "kind": "video",
                          "preview_url": _asset_url(cid), "status": "ready", "cost": clip["cost"]})

        # VO track (chosen voice, final tier)
        _working[thread_id] = "rendering VO track"
        vo = generate("audio", script["vo_script"], tier="final", voice=ws["voice"])
        vid = store.add_asset(thread_id, "vo_track", "audio", vo["path"],
                              {"model": vo["model"], "voice": ws["voice"]}, vo["cost"])
        store.log_generation(thread_id, vid, "generate", model=vo["model"], cost=vo["cost"])
        ws["spent"] += vo["cost"]
        ws["assets"]["vo_track"] = vid
        items.append({"asset_id": vid, "slot": "vo_track", "kind": "audio",
                      "preview_url": _asset_url(vid), "status": "ready", "cost": vo["cost"]})

        # §07 seam QA (mock heuristic on shot boundaries): first fail → ONE
        # free auto re-roll; a second fail would surface as a choice card.
        seam_note = None
        if not ws["draft_only"] and config.MOCK_MEDIA and "shot_02" in ws["assets"] and "shot_02" not in ws["seam_rerolled"]:
            ws["seam_rerolled"].add("shot_02")
            store.log_generation(thread_id, ws["assets"]["shot_02"], "seam_qa",
                                 prompt="boundary shot_02/shot_03 flagged: lighting jump")
            store.log_generation(thread_id, ws["assets"]["shot_02"], "reroll", prompt="auto re-roll (free pass)")
            seam_note = "Seam QA flagged the shot 2/3 boundary — auto re-rolled once ✓."

        ws["endcards"] = director.end_card_options(store.get_profile())
        end = ArtifactEnvelope(
            type="asset_set", id="endcards", title="End-cards — from brand kit",
            payload={"slot": "end_card",
                     "items": [{"asset_id": f"end_{i+1}", "slot": f"end_{i+1}", "kind": "image",
                                "preview_url": "", "status": "ready", "cost": 0.0, "note": text}
                               for i, text in enumerate(ws["endcards"])]},
            actions=_actions(("pick_end_1", "1", "secondary"), ("pick_end_2", "2", "primary"),
                             ("pick_end_3", "3", "secondary")),
        )
        assets_art = ArtifactEnvelope(
            type="asset_set", id="assets", title="Rendered shots + VO",
            payload={"slot": "shots", "items": items},
            actions=_actions(("accept_all", "Accept all", "primary")),
        )
        msg = "Rendered — frames → shots → VO."
        if seam_note:
            msg = f"{msg} {seam_note}"
        if ws["draft_only"]:
            msg = f"{msg} Draft mode: shot 1 only — generate the rest when the look is right."
        _say(thread_id, msg[:278], [assets_art, end],
             question="Re-roll any shot, or pick an end-card to finish?")
        store.set_thread_stage(thread_id, "endcard" if not ws["draft_only"] else "review_assets")
        if ws["draft_only"]:
            # offer completing the set
            ws["draft_only"] = False
            store.set_thread_stage(thread_id, "prompts")
            _say(thread_id, "When ready: generate the remaining shots.",
                 [ArtifactEnvelope(type="asset_prompt", id="generate_gate2", title="Generate remaining 3 shots",
                                   payload={"asset_slot": "rest", "model": "batch", "prompt_text": "",
                                            "cost": round(sum(estimate_cost('image', tier='final') for _ in shots[1:])
                                                          + estimate_cost('video', duration_s=sum(s['duration_s'] for s in shots[1:])), 2),
                                            "ratio": "9:16", "locks": []},
                                   actions=_actions(("generate_all", "Generate remaining", "primary")))])
    except MediaError as exc:
        kind = "the model declined this prompt — edit it and retry" if exc.policy else "provider error (not charged)"
        _fail(thread_id, f"Generation stopped: {kind}. {exc}", "")
    except Exception as exc:
        _fail(thread_id, f"Generation error: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


def _reroll_asset(thread_id: str, slot: str, note: Optional[str]) -> None:
    try:
        ws = _pending(thread_id)
        shot = next((s for s in ws["script"]["shots"] if s["shot_id"] == slot), None)
        if not shot or slot not in ws["assets"]:
            _say(thread_id, f"No rendered asset for {slot}.")
            return
        _working[thread_id] = f"re-rolling {slot}"
        cost = estimate_cost("video", duration_s=shot["duration_s"])
        clip = generate("video", shot["motion_prompt"] + (f". Adjustment: {note}" if note else ""),
                        duration_s=shot["duration_s"], seed=int(time.time()) % 10_000)
        cid = store.add_asset(thread_id, slot, "video", clip["path"],
                              {"model": clip["model"], "reroll": True}, clip["cost"])
        store.log_generation(thread_id, cid, "reroll", prompt=note, model=clip["model"], cost=clip["cost"])
        ws["spent"] += clip["cost"]
        ws["assets"][slot] = cid
        ws["accepted"].discard(slot)
        _say(thread_id, f"{slot} re-rolled (${clip['cost']:.2f}).",
             [ArtifactEnvelope(type="asset_set", id="assets", title=f"{slot} — new take",
                               payload={"slot": slot,
                                        "items": [{"asset_id": cid, "slot": slot, "kind": "video",
                                                   "preview_url": _asset_url(cid), "status": "ready",
                                                   "cost": clip["cost"]}]},
                               actions=_actions(("accept_all", "Accept", "primary"),
                                                (f"reroll_{slot}", "Re-roll again", "secondary")))])
    except MediaError as exc:
        _fail(thread_id, f"Re-roll failed: {exc}", "")
    finally:
        _working.pop(thread_id, None)


def _assemble_turn(thread_id: str) -> None:
    try:
        _working[thread_id] = "assembling the Post Card"
        ws = _pending(thread_id)
        series_id, concept = ws["series_id"], ws["concept"]
        context = store.get_series(series_id)["context"]
        platforms = context.get("platforms", ["instagram_reels"])
        content = director.captions(concept, platforms, store.get_profile())
        total_s = sum(s["duration_s"] for s in ws["script"]["shots"])

        media = []
        for slot, aid in ws["assets"].items():
            asset = store.get_asset(aid)
            if not asset:
                continue
            media.append({
                "kind": asset["kind"], "ratio": "9:16",
                "duration_s": total_s if slot == "vo_track" else None,
                "url": _asset_url(aid), "cover_url": None,
                "params": {"model": asset["params"].get("model"), "prompt_id": slot,
                           "cost": asset["cost"]},
            })

        card_id = store.new_id("post")
        card = PostCard(
            id=card_id, series_id=series_id, thread_id=thread_id,
            concept_id=concept["id"], option=ws["option_id"],
            format=concept["format"], platforms=platforms,
            post_content={**content, "cta": ws.get("endcard") or content["cta"]},
            media=media,
            total_cost_credits=round(ws["spent"] / CREDIT_USD, 1),
            status="ready", created_at=time.time(),
            generation_log_ref=f"/api/threads/{thread_id}/generation-log",
        ).model_dump(mode="json")
        store.save_post_card(card)
        ws["card_id"] = card_id
        store.set_production_status(series_id, concept["id"], "ready")

        nxt = _next_planned_concept(series_id)
        _say(thread_id, "Done — your Post Card. Pinned to its Plans slot and saved in My Space.",
             [ArtifactEnvelope(type="post_card", id=card_id, title=f"Post Card · {concept['title'][:60]}",
                               payload=card,
                               actions=_actions(("mark_posted", "Mark Posted", "primary")))],
             question=f"Next in plan: {nxt} — start its thread?" if nxt else None)
        store.set_thread_stage(thread_id, "done")
        store.log_artifact_activity(thread_id, card_id, "proposed",
                                    f"{len(media)} assets · {card['total_cost_credits']} credits")
    except Exception as exc:
        _fail(thread_id, f"Post Card assembly failed: {exc}", traceback.format_exc())
    finally:
        _working.pop(thread_id, None)


# ----------------------------------------------------------------- helpers --


def _copy_only_card(series_id: str, concept: dict, context: dict) -> dict[str, Any]:
    content = director.captions(concept, context.get("platforms", []), store.get_profile())
    card_id = store.new_id("post")
    card = PostCard(
        id=card_id, series_id=series_id, thread_id="", concept_id=concept["id"], option="A",
        format=concept["format"], platforms=context.get("platforms", []),
        post_content=content, media=[], total_cost_credits=0.0,
        status="ready", created_at=time.time(), generation_log_ref="",
    ).model_dump(mode="json")
    store.save_post_card(card)
    store.set_production_status(series_id, concept["id"], "ready")
    return card


def _next_planned_concept(series_id: str) -> Optional[str]:
    for s in store.get_concept_states(series_id):
        if (s.get("production_status") or "planned") == "planned" and s["approved"]:
            return s["concept_id"]
    for s in store.get_concept_states(series_id):
        if (s.get("production_status") or "planned") == "planned":
            return s["concept_id"]
    return None


def _fail(thread_id: str, summary: str, detail: str) -> None:
    _say(thread_id, "Something broke — honestly.",
         [ArtifactEnvelope(type="escalation", id="error", title=summary[:120],
                           payload={"reason": summary, "below_threshold_count": 0,
                                    "total_concepts": 1, "choices": ["accept_provisional"]},
                           actions=_actions(("retry", "Retry", "primary")))])
