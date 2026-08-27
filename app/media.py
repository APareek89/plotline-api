"""Media generation — provider routing over PixelBin (primary) and fal (fallback).

Owner decision 2026-08-26: PixelBin generates, fal stays wired as the fallback.
`PLOTLINE_MEDIA_PROVIDER` names which is tried FIRST; the other is reached only
when the first raises. Suffix `_only` disables the fallback.

The fallback is deliberately NOT attempted on a policy refusal. A model that
declined a prompt is giving a real answer about the prompt, and re-sending the
same words to a second provider spends money to be told the same thing. Only
transport and provider-side failures fall through.

fal queue API: POST queue.fal.run/{model} → poll status → fetch result. No
webhooks in dev — the driver polls (thread stays usable; completion posts a
message). PixelBin's REST contract lives in `pixelbin_client.py`.

MOCK_MEDIA=1: deterministic placeholder files (SVG frames, ffmpeg color
clips, sine-wave WAV) written to data/assets/ at ZERO cost — the entire
interaction contract (prompt artifacts, cost lines, accept/reroll) still
runs, and neither provider is contacted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Optional

import httpx

from app import config

logger = logging.getLogger("plotline.media")

QUEUE = "https://queue.fal.run"


class MediaError(RuntimeError):
    """Provider failure after retry, or policy rejection — surfaced honestly,
    never silently swallowed. `policy=True` → the model declined the prompt."""

    def __init__(self, message: str, policy: bool = False):
        self.policy = policy
        super().__init__(message)


def estimate_cost(kind: str, *, duration_s: float = 0, chars: int = 0, tier: str = "draft") -> float:
    c = config.MEDIA_COST_USD
    if kind == "image":
        return c[config.media_key("image", tier)]
    if kind == "video":
        return round(c["video_per_s"] * duration_s, 2)
    if kind == "audio":
        per_1k = c["tts_draft_per_1k"] if tier == "draft" else c["tts_final_per_1k"]
        return round(per_1k * max(1, chars) / 1000, 3)
    return 0.0


def _headers() -> dict[str, str]:
    if not config.FAL_KEY:
        raise MediaError("FAL_KEY is not set — real media generation unavailable (set MOCK_MEDIA=1 to develop without it)")
    return {"Authorization": f"Key {config.FAL_KEY}", "Content-Type": "application/json"}


def _submit_and_wait(model: str, payload: dict[str, Any], timeout_s: float = 300) -> dict[str, Any]:
    """One bounded retry on transport/5xx; 4xx (validation/policy) never retried."""
    last: Optional[Exception] = None
    for attempt in range(2):
        try:
            sub = httpx.post(f"{QUEUE}/{model}", headers=_headers(), json=payload, timeout=30)
            if sub.status_code == 422:
                raise MediaError(f"{model} rejected the request: {sub.text[:300]}", policy=True)
            sub.raise_for_status()
            job = sub.json()
            status_url = job.get("status_url") or f"{QUEUE}/{model}/requests/{job['request_id']}/status"
            response_url = job.get("response_url") or f"{QUEUE}/{model}/requests/{job['request_id']}"
            started = time.time()
            while time.time() - started < timeout_s:
                st = httpx.get(status_url, headers=_headers(), timeout=20).json()
                status = st.get("status")
                if status == "COMPLETED":
                    res = httpx.get(response_url, headers=_headers(), timeout=30)
                    res.raise_for_status()
                    return res.json()
                if status in ("FAILED", "ERROR", "CANCELLED"):
                    detail = json.dumps(st)[:300]
                    if "content" in detail.lower() or "policy" in detail.lower() or "safety" in detail.lower():
                        raise MediaError(f"the model declined this prompt: {detail}", policy=True)
                    raise MediaError(f"{model} job failed: {detail}")
                time.sleep(2)
            raise MediaError(f"{model} timed out after {timeout_s}s")
        except MediaError:
            raise
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last = exc
            logger.warning("fal %s attempt %d failed (%s) — %s", model, attempt + 1, type(exc).__name__, exc)
            if attempt == 0:
                time.sleep(1.5)  # §07: one automatic retry, silent
    raise MediaError(f"{model} unreachable after retry: {last}")


def _download(url: str, dest: Path) -> Path:
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
    return dest


def _slug(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:10]


# ------------------------------------------------------------- mock makers --


_RATIO_PX = {"9:16": (540, 960), "1:1": (720, 720), "16:9": (960, 540)}


def _mock_image(prompt: str, ratio: str, dest: Path) -> Path:
    w, h = _RATIO_PX.get(ratio, (540, 960))
    hue = int(_slug(prompt), 16) % 360
    label = (prompt[:110] + "…") if len(prompt) > 110 else prompt
    words, lines, cur = label.split(), [], ""
    for word in words:
        if len(cur) + len(word) > 26:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    lines.append(cur)
    tspans = "".join(
        f'<tspan x="{w//2}" dy="{22 if i else 0}">{ln}</tspan>' for i, ln in enumerate(lines[:8])
    )
    dest.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<rect width="{w}" height="{h}" fill="hsl({hue},38%,22%)"/>'
        f'<rect x="16" y="16" width="{w-32}" height="{h-32}" fill="none" stroke="hsl({hue},50%,55%)" stroke-dasharray="8 6" stroke-width="2"/>'
        f'<text x="{w//2}" y="{h//2 - len(lines)*10}" fill="#EDEFF3" font-family="monospace" font-size="15" text-anchor="middle">{tspans}</text>'
        f'<text x="{w//2}" y="{h-34}" fill="hsl({hue},50%,65%)" font-family="monospace" font-size="12" text-anchor="middle">MOCK RENDER — no cost incurred</text>'
        "</svg>"
    )
    return dest


def _mock_audio(text: str, dest: Path) -> Path:
    """Audible placeholder: soft tone sequence roughly the length of the VO."""
    rate, seconds = 22050, min(20, max(2, len(text) / 15))
    with wave.open(str(dest), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        base = 220 + int(_slug(text), 16) % 220
        for i in range(int(rate * seconds)):
            t = i / rate
            f = base + 40 * math.sin(2 * math.pi * 0.5 * t)
            amp = 0.18 * (1 - abs((t % 2) - 1))
            wf.writeframes(struct.pack("<h", int(32767 * amp * math.sin(2 * math.pi * f * t))))
    return dest


def _mock_video(prompt: str, ratio: str, duration_s: float, dest: Path) -> Path:
    import colorsys

    w, h = _RATIO_PX.get(ratio, (540, 960))
    hue = int(_slug(prompt), 16) % 360
    r, g, b = colorsys.hls_to_rgb(hue / 360, 0.25, 0.40)
    hexcol = f"0x{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", f"color=c={hexcol}:s={w}x{h}:d={duration_s}",
             "-vf", f"drawtext=text='MOCK SHOT {_slug(prompt)}':fontcolor=white:fontsize=20:x=(w-text_w)/2:y=(h-text_h)/2",
             "-pix_fmt", "yuv420p", str(dest)],
            check=True, timeout=60,
        )
    except Exception:
        # drawtext unavailable → plain color clip, still a real mp4
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", f"color=c={hexcol}:s={w}x{h}:d={duration_s}",
             "-pix_fmt", "yuv420p", str(dest)],
            check=True, timeout=60,
        )
    return dest


# ---------------------------------------------------------------- generate --



# Per-model video capability table, read from fal's LIVE OpenAPI on 2026-08-26:
#   GET https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=<model>
#
# These models do NOT share a parameter set, and the differences decide which
# one a shot can even use:
#
#   model                              ref field(s)              durations
#   seedance/v1/pro/image-to-video     image_url + end_image_url "2".."12"  <- any
#   veo3.1/image-to-video              image_url                 "4s","6s","8s"
#   veo3.1/reference-to-video          image_urls[]  (ARRAY)     free string
#   kling-video/v2.1/master/i2v        image_url                 "5","10"
#   kling-video/v1.6/pro/i2v           image_url + tail_image_url "5","10"
#   kling-video/v1.6/pro/elements      input_image_urls[] (ARRAY) "5","10"
#   minimax/hailuo-02/standard/i2v     image_url + end_image_url "6","10"
#   pixverse/v4.5/image-to-video       image_url                 "5","8"
#   wan-i2v                            image_url                 num_frames
#
# Note veo3.1 spells its durations WITH the unit ("4s") and everyone else
# without ("4"). Sending the wrong spelling is a 422 that reads like an outage,
# which is the dishonest-error class this codebase keeps stamping out.
#
# `end_field` matters for stitching: a shot whose clip ends ON the next shot's
# approved keyframe cuts without a jump. Available, deliberately not automatic —
# whether two beats should morph or cut is a creative call, not a wiring one.
_FAL_VIDEO_CAPS: dict[str, dict[str, Any]] = {
    "fal-ai/bytedance/seedance/v1/pro/image-to-video": {
        "image_field": "image_url", "array": False, "end_field": "end_image_url",
        "durations": {"2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"},
        "unit": "", "ratios": {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "auto"},
        "resolution_field": "resolution", "resolutions": {"480p", "720p", "1080p"},
    },
    "fal-ai/veo3.1/image-to-video": {
        "image_field": "image_url", "array": False, "end_field": None,
        "durations": {"4", "6", "8"}, "unit": "s",
        "ratios": {"auto", "16:9", "9:16"},
        "resolution_field": "resolution", "resolutions": {"720p", "1080p", "4k"},
    },
    "fal-ai/veo3.1/fast/image-to-video": {
        "image_field": "image_url", "array": False, "end_field": None,
        "durations": {"4", "6", "8"}, "unit": "s",
        "ratios": {"auto", "16:9", "9:16"},
        "resolution_field": "resolution", "resolutions": {"720p", "1080p", "4k"},
    },
    "fal-ai/veo3.1/reference-to-video": {
        "image_field": "image_urls", "array": True, "end_field": None,
        "durations": {"4", "6", "8"}, "unit": "s",
        "ratios": {"16:9", "9:16"}, "resolution_field": None, "resolutions": set(),
    },
    "fal-ai/kling-video/v2.1/master/image-to-video": {
        "image_field": "image_url", "array": False, "end_field": None,
        "durations": {"5", "10"}, "unit": "",
        "ratios": {"16:9", "9:16", "1:1"}, "resolution_field": None, "resolutions": set(),
    },
    "fal-ai/kling-video/v1.6/pro/image-to-video": {
        "image_field": "image_url", "array": False, "end_field": "tail_image_url",
        "durations": {"5", "10"}, "unit": "",
        "ratios": {"16:9", "9:16", "1:1"}, "resolution_field": None, "resolutions": set(),
    },
    "fal-ai/minimax/hailuo-02/standard/image-to-video": {
        "image_field": "image_url", "array": False, "end_field": "end_image_url",
        "durations": {"6", "10"}, "unit": "",
        "ratios": set(), "resolution_field": None, "resolutions": set(),
    },
    "fal-ai/pixverse/v4.5/image-to-video": {
        "image_field": "image_url", "array": False, "end_field": None,
        "durations": {"5", "8"}, "unit": "",
        "ratios": set(), "resolution_field": None, "resolutions": set(),
    },
    "fal-ai/kling-video/v1.6/pro/elements": {
        "image_field": "input_image_urls", "array": True, "end_field": None,
        "durations": {"5", "10"}, "unit": "",
        "ratios": {"16:9", "9:16", "1:1"}, "resolution_field": None, "resolutions": set(),
    },
    "fal-ai/wan-i2v": {
        "image_field": "image_url", "array": False, "end_field": None,
        "durations": set(), "unit": "",
        "ratios": {"auto", "16:9", "9:16", "1:1"}, "resolution_field": None, "resolutions": set(),
    },
}


def _nearest(value: float, allowed: set[str]) -> str:
    """Nearest allowed duration, as the STRING its enum declares."""
    return min(allowed, key=lambda d: abs(int(d) - value))


def _generate_fal(
    kind: str, prompt: str, *, ratio: str, duration_s: float, tier: str,
    voice: Optional[str], image_urls: list[str], seed: Optional[int],
) -> tuple[str, str, list[dict[str, str]]]:
    """One asset via fal. Returns (url, model, dropped). Raises MediaError.

    `dropped` names every reference fal could NOT carry, and why. fal's image
    models are wired here as text-to-image only — there is no verified
    reference field on that path — and its video model takes ONE `image_url`.
    Both are real limits of the FALLBACK provider, so a PixelBin failover with
    references attached silently becomes a different render unless it says so.
    """
    dropped: list[dict[str, str]] = []
    if kind == "image":
        model = config.MEDIA_MODELS[config.media_key("image", tier)]
        payload: dict[str, Any] = {"prompt": prompt, "aspect_ratio": ratio, "num_images": 1}
        dropped += [{"url": u, "why": f"{model} is wired text-to-image here — "
                                      "fal's reference field is not verified on this path"}
                    for u in image_urls]
        if seed is not None:
            payload["seed"] = seed
        out = _submit_and_wait(model, payload)
        url = (out.get("images") or [{}])[0].get("url") or out.get("image", {}).get("url")
        if not url:
            raise MediaError(f"{model} returned no image: {json.dumps(out)[:200]}")
    elif kind == "audio":
        model = config.MEDIA_MODELS["tts_draft" if tier == "draft" else "tts_final"]
        payload = {"text": prompt}
        if voice:
            payload["voice"] = voice
        out = _submit_and_wait(model, payload)
        url = out.get("audio", {}).get("url") or out.get("audio_url", {}).get("url") or out.get("audio_file", {}).get("url")
        if not url:
            raise MediaError(f"{model} returned no audio: {json.dumps(out)[:200]}")
    else:
        model = config.MEDIA_MODELS["video"]
        caps = _FAL_VIDEO_CAPS.get(model)
        if caps is None:
            raise MediaError(
                f"{model} is not in the fal video capability table — its parameter set is "
                "unknown, and a guessed parameter is a 422 that reads like an outage. "
                "Read its schema from fal's OpenAPI and add a row.")
        payload = {"prompt": prompt}
        if caps["ratios"]:
            payload["aspect_ratio"] = ratio if ratio in caps["ratios"] else "9:16"
        if caps["durations"]:
            payload["duration"] = _nearest(duration_s, caps["durations"]) + caps["unit"]
        if caps["resolutions"]:
            payload[caps["resolution_field"]] = "720p"
        if image_urls:
            if caps["array"]:
                payload[caps["image_field"]] = list(image_urls)
            else:
                payload[caps["image_field"]] = image_urls[0]
                extra = image_urls[1:]
                if extra and caps["end_field"]:
                    # A second reference is the END frame on models that take
                    # one. Not a drop: it is the other half of the shot.
                    payload[caps["end_field"]] = extra[0]
                    extra = extra[1:]
                dropped += [{"url": u, "why": f"{model} seeds from one start frame"
                                              + (" and one end frame" if caps["end_field"] else "")}
                            for u in extra]
        out = _submit_and_wait(model, payload, timeout_s=600)
        url = out.get("video", {}).get("url")
        if not url:
            raise MediaError(f"{model} returned no video: {json.dumps(out)[:200]}")
    return url, f"fal:{model}", dropped


def _generate_pixelbin(
    kind: str, prompt: str, *, ratio: str, duration_s: float, tier: str,
    image_urls: list[str], resolution: Optional[str],
) -> tuple[str, str, list[dict[str, str]]]:
    """One asset via PixelBin. Returns (url, model, dropped). Raises MediaError,
    so the router does not have to know two exception types.

    PixelBin drops nothing of its own: the slot budget is applied ONCE, in
    generate(), against `config.MEDIA_REF_SLOTS` — the same table the board's
    B3 lint reads. A second cap down here would be a second representation of
    one number, which is how the board came to police a capacity the client
    could not use in the first place.
    """
    from app import pixelbin_client

    try:
        out = pixelbin_client.generate(
            kind, prompt, ratio=ratio, duration_s=duration_s, tier=tier,
            image_urls=image_urls, resolution=resolution,
        )
    except pixelbin_client.PixelbinError as exc:
        raise MediaError(str(exc), policy=exc.policy) from exc
    return out["url"], out["model"], []


def _provider_order(kind: str) -> list[str]:
    """Which providers to try, in order. PixelBin has no TTS operation, so
    audio goes straight to fal rather than failing at the provider with a
    message about a plugin that was never going to exist."""
    if kind == "audio":
        return ["fal"]
    pref = config.MEDIA_PROVIDER
    if pref.endswith("_only"):
        return [pref[:-5]]
    return ["pixelbin", "fal"] if pref == "pixelbin" else ["fal", "pixelbin"]


def generate(
    kind: str,  # image | video | audio
    prompt: str,
    *,
    ratio: str = "9:16",
    duration_s: float = 4.0,
    tier: str = "final",
    voice: Optional[str] = None,
    image_urls: Optional[list[str]] = None,
    resolution: Optional[str] = None,
    seed: Optional[int] = None,
) -> dict[str, Any]:
    """Returns {path?, url?, model, cost, seed, mock, fallback?, refs_used,
    dropped_refs}. Local files live under data/assets/ and are served by
    /api/assets.

    `model` carries its provider prefix ("pixelbin:…" / "fal:…") because the
    generation_log is an audit surface: "which provider actually made this"
    is exactly the question a cost dispute asks, and a bare model name cannot
    answer it once two providers can produce the same asset.

    `image_urls` is a LIST, and the list is the point. `config.MEDIA_REF_SLOTS`
    has declared 2–6 reference slots per model since v3 and the board's B3 lint
    polices shots against those numbers — while this function accepted exactly
    one URL. The lint was enforcing a capacity the client could not use.

    Over-supply is trimmed HERE, once, and every dropped reference is returned
    in `dropped_refs` with the reason it went. A silently dropped product
    reference is precisely how label and geometry drift enter a campaign
    (v3 §5, B3), so the drop is a fact the caller can say out loud, never a
    log line nobody reads.
    """
    cost = estimate_cost(kind, duration_s=duration_s, chars=len(prompt), tier=tier)
    name = f"{kind}_{_slug(prompt + str(seed or 0))}"

    # Trim before the mock branch, not after: the whole interaction contract —
    # what gets sent, what gets dropped, what the user is told — has to be
    # identical at zero spend, or MOCK_MEDIA stops being a rehearsal.
    refs = [u for u in (image_urls or []) if u]
    budget = config.ref_slots(kind, tier)
    kept, over = refs[:budget], refs[budget:]
    dropped: list[dict[str, str]] = [
        {"url": u, "why": f"over the {budget}-slot reference budget for "
                          f"{config.media_key(kind, tier)}"}
        for u in over
    ]

    if config.MOCK_MEDIA:
        import shutil as _sh

        out_kind = kind
        ext = {"image": "svg", "video": "mp4", "audio": "wav"}[kind]
        if kind == "video" and not _sh.which("ffmpeg"):
            # no ffmpeg (e.g. Render) → honest SVG poster instead of a broken mp4
            out_kind, ext = "image", "svg"
        dest = config.ASSET_DIR / f"{name}.{ext}"
        if out_kind == "image":
            _mock_image(prompt if kind == "image" else f"[video poster — ffmpeg unavailable] {prompt}", ratio, dest)
        elif kind == "audio":
            _mock_audio(prompt, dest)
        else:
            _mock_video(prompt, ratio, duration_s, dest)
        logger.info("mock %s generated (%s) — $0.00", kind, dest.name)
        return {"path": str(dest), "model": "mock", "cost": 0.0, "seed": seed, "mock": True,
                "kind": out_kind, "refs_used": kept, "dropped_refs": dropped}

    order = _provider_order(kind)
    url = model = ""
    failures: list[str] = []
    for idx, provider in enumerate(order):
        try:
            if provider == "pixelbin":
                url, model, by_provider = _generate_pixelbin(
                    kind, prompt, ratio=ratio, duration_s=duration_s, tier=tier,
                    image_urls=kept, resolution=resolution)
            else:
                url, model, by_provider = _generate_fal(
                    kind, prompt, ratio=ratio, duration_s=duration_s, tier=tier,
                    voice=voice, image_urls=kept, seed=seed)
            # The provider may refuse references the budget allowed — fal's
            # video model seeds from one URL, its image path from none. That is
            # a SECOND, different drop and it is reported the same way.
            dropped += by_provider
            kept = [u for u in kept if u not in {d["url"] for d in by_provider}]
            break
        except MediaError as exc:
            # A refused prompt is a real answer about the prompt. Asking a
            # second provider the same question costs money to hear it again.
            if exc.policy:
                raise
            failures.append(f"{provider}: {exc}")
            if idx == len(order) - 1:
                raise MediaError(" · ".join(failures)) from exc
            logger.warning("media: %s failed, falling back to %s — %s",
                           provider, order[idx + 1], exc)

    ext = {"image": "png", "video": "mp4", "audio": "mp3"}[kind]
    try:
        dest = _download(url, config.ASSET_DIR / f"{name}.{ext}")
    except Exception as exc:
        # The provider has ALREADY generated and billed by this point. Losing
        # the local copy must not also lose the RECORD of the spend, or the
        # money becomes invisible: no asset, no generation_log row, nothing for
        # a cost dispute to point at. One retry first — a 5 MB video over a
        # proxy is the common case — then record the spend and say what
        # happened, including the URL the render still lives at.
        try:
            dest = _download(url, config.ASSET_DIR / f"{name}.{ext}")
        except Exception as second:
            _record_orphan_spend(kind, model, cost, url, prompt)
            raise MediaError(
                f"{model} generated this {kind} and it was charged (~${cost:.2f}), but the file "
                f"could not be downloaded after a retry ({type(second).__name__}). The render is "
                f"still at {url} — the spend is recorded so it is not invisible."
            ) from second

    if dropped:
        logger.warning("%s: %d reference(s) dropped — %s", model, len(dropped),
                       "; ".join(d["why"] for d in dropped))
    logger.info("%s generated via %s — est $%.2f (%d ref%s)", kind, model, cost,
                len(kept), "" if len(kept) == 1 else "s")
    return {"path": str(dest), "url": url, "model": model, "cost": cost, "seed": seed,
            "mock": False, "fallback": bool(failures),
            "refs_used": kept, "dropped_refs": dropped}


def _record_orphan_spend(kind: str, model: str, cost: float, url: str, prompt: str) -> None:
    """Log a render that was paid for but never landed locally.

    Written from here rather than from the caller on purpose: the caller's
    logging runs AFTER generate() returns, so an exception on the way out skips
    it — which is exactly how a paid render became invisible. The thread comes
    from the contextvar the runner already sets per worker.
    """
    try:
        from app import store
        from app.agents.runner import current_thread

        store.log_generation(
            current_thread.get(), None, f"{kind}_orphaned",
            prompt=prompt[:500], model=model, cost=cost,
        )
        logger.error("orphaned %s spend ~$%.2f via %s — file never downloaded, url=%s",
                     kind, cost, model, url)
    except Exception:  # noqa: BLE001 — logging a loss must never mask the loss
        logger.exception("could not even record the orphaned %s spend (~$%.2f)", kind, cost)


# ------------------------------------------------------------------ stitch --


def ffmpeg_available() -> bool:
    """ffmpeg exists on this machine but NOT on Render, so every path that uses
    it has to have an answer for its absence rather than a traceback."""
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            check=True, timeout=30, capture_output=True, text=True)
        return round(float(out.stdout.strip()), 3)
    except Exception:
        return 0.0


def stitch(clips: list[dict[str, Any]], *, audio_path: Optional[str] = None,
           name: str = "film") -> dict[str, Any]:
    """Concatenate N clips into ONE film, degrading honestly.

    `clips` is [{slot, path}] in cut order. Returns
    {path, duration_s, joined, missing, audio, degraded, note}.

    Two rules, both taken from precedent in this module rather than invented:

    1. **It degrades, it does not crash.** A clip whose file is gone is skipped
       and NAMED; the rest are still delivered. Handing back nothing because one
       of six shots is missing throws away five paid renders, which is the same
       failure `_partial_fail` exists to prevent on the render path.
    2. **No ffmpeg is a supported state.** It exists on this machine and not on
       Render, exactly as `_mock_video` already assumes. Without it there is no
       film, and the clips are handed over individually with that said out loud
       — never a half-file that looks like a delivery.

    The concat DEMUXER is used rather than the filter, because these clips come
    from one model at one ratio and re-encoding N clips to join them would cost
    quality for nothing. If a stream mismatch makes the demuxer fail, it falls
    back to re-encoding once and says so.
    """
    ordered = [c for c in clips if c.get("path")]
    missing = [c.get("slot") or "?" for c in clips if not c.get("path")]
    present: list[dict[str, Any]] = []
    for clip in ordered:
        if Path(clip["path"]).exists():
            present.append(clip)
        else:
            missing.append(clip.get("slot") or "?")

    if not present:
        return {"path": None, "duration_s": 0.0, "joined": [], "missing": missing,
                "audio": False, "degraded": True,
                "note": "nothing to stitch — no clip file was on disk"}

    if not ffmpeg_available():
        return {"path": None, "duration_s": 0.0, "joined": [], "missing": missing,
                "audio": False, "degraded": True,
                "note": ("ffmpeg is not installed here, so the clips are handed over "
                         "individually rather than as one film")}

    dest = config.ASSET_DIR / f"{name}_{_slug(''.join(c['path'] for c in present))}.mp4"
    listing = dest.with_suffix(".txt")
    listing.write_text("".join(
        "file '{}'\n".format(str(Path(c["path"]).resolve()).replace("'", r"'\''"))
        for c in present))

    def _run(args: list[str]) -> None:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                       check=True, timeout=600)

    concat = ["-f", "concat", "-safe", "0", "-i", str(listing)]
    degraded = bool(missing)
    note_bits: list[str] = []
    try:
        _run([*concat, "-c", "copy", str(dest)])
    except Exception:
        # Streams that do not match cannot be copied; one re-encode is worth a
        # film, but the user is told the quality was touched.
        _run([*concat, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)])
        degraded = True
        note_bits.append("clips did not share a stream format, so they were re-encoded once")

    audio_ok = False
    if audio_path and Path(audio_path).exists():
        with_audio = dest.with_name(dest.stem + "_av.mp4")
        try:
            _run(["-i", str(dest), "-i", str(audio_path), "-c:v", "copy", "-c:a", "aac",
                  "-shortest", "-map", "0:v:0", "-map", "1:a:0", str(with_audio)])
            dest, audio_ok = with_audio, True
        except Exception:
            # A film with no bed is still the film. Losing the cut because the
            # audio would not mux is the tail wagging the dog.
            degraded = True
            note_bits.append("the audio bed could not be muxed, so the film is silent")
    elif audio_path:
        degraded = True
        note_bits.append("the audio bed file was missing, so the film is silent")

    listing.unlink(missing_ok=True)
    if missing:
        note_bits.insert(0, "missing " + ", ".join(missing))
    return {
        "path": str(dest), "duration_s": _probe_duration(dest),
        "joined": [c.get("slot") or "?" for c in present], "missing": missing,
        "audio": audio_ok, "degraded": degraded,
        "note": "; ".join(note_bits),
    }
