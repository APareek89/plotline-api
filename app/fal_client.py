"""fal.ai media client (Addendum-02; models per v1 §07).

Queue API: POST queue.fal.run/{model} → poll status → fetch result. No
webhooks in Phase-2 dev — the driver polls (thread stays usable; completion
posts a message).

MOCK_MEDIA=1: deterministic placeholder files (SVG frames, ffmpeg color
clips, sine-wave WAV) written to data/assets/ at ZERO cost — the entire
interaction contract (prompt artifacts, cost lines, accept/reroll) still
runs. Failed real runs are not charged (fal bills completed jobs).
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
        return c[f"image_{tier}"] if f"image_{tier}" in c else c["image_final"]
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
    w, h = _RATIO_PX.get(ratio, (540, 960))
    hue = int(_slug(prompt), 16) % 360
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", f"color=c=hsl({hue}\\,40%\\,25%):s={w}x{h}:d={duration_s}",
             "-vf", f"drawtext=text='MOCK SHOT — {_slug(prompt)}':fontcolor=white:fontsize=20:x=(w-text_w)/2:y=(h-text_h)/2",
             "-pix_fmt", "yuv420p", str(dest)],
            check=True, timeout=60,
        )
    except Exception:
        # no ffmpeg / drawtext missing → plain color clip, still a real mp4
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", f"color=c=gray:s={w}x{h}:d={duration_s}",
             "-pix_fmt", "yuv420p", str(dest)],
            check=True, timeout=60,
        )
    return dest


# ---------------------------------------------------------------- generate --


def generate(
    kind: str,  # image | video | audio
    prompt: str,
    *,
    ratio: str = "9:16",
    duration_s: float = 4.0,
    tier: str = "final",
    voice: Optional[str] = None,
    image_url: Optional[str] = None,
    seed: Optional[int] = None,
) -> dict[str, Any]:
    """Returns {path?, url?, model, cost, seed, mock}. Local files live under
    data/assets/ and are served by /api/assets."""
    cost = estimate_cost(kind, duration_s=duration_s, chars=len(prompt), tier=tier)
    name = f"{kind}_{_slug(prompt + str(seed or 0))}"

    if config.MOCK_MEDIA:
        ext = {"image": "svg", "video": "mp4", "audio": "wav"}[kind]
        dest = config.ASSET_DIR / f"{name}.{ext}"
        if kind == "image":
            _mock_image(prompt, ratio, dest)
        elif kind == "audio":
            _mock_audio(prompt, dest)
        else:
            _mock_video(prompt, ratio, duration_s, dest)
        logger.info("mock %s generated (%s) — $0.00", kind, dest.name)
        return {"path": str(dest), "model": "mock", "cost": 0.0, "seed": seed, "mock": True}

    if kind == "image":
        model = config.MEDIA_MODELS["image_draft" if tier == "draft" else ("image_pro" if tier == "pro" else "image_final")]
        payload: dict[str, Any] = {"prompt": prompt, "aspect_ratio": ratio, "num_images": 1}
        if seed is not None:
            payload["seed"] = seed
        out = _submit_and_wait(model, payload)
        url = (out.get("images") or [{}])[0].get("url") or out.get("image", {}).get("url")
        if not url:
            raise MediaError(f"{model} returned no image: {json.dumps(out)[:200]}")
        dest = _download(url, config.ASSET_DIR / f"{name}.png")
    elif kind == "audio":
        model = config.MEDIA_MODELS["tts_draft" if tier == "draft" else "tts_final"]
        payload = {"text": prompt}
        if voice:
            payload["voice"] = voice
        out = _submit_and_wait(model, payload)
        url = out.get("audio", {}).get("url") or out.get("audio_url", {}).get("url") or out.get("audio_file", {}).get("url")
        if not url:
            raise MediaError(f"{model} returned no audio: {json.dumps(out)[:200]}")
        dest = _download(url, config.ASSET_DIR / f"{name}.mp3")
    else:
        model = config.MEDIA_MODELS["video"]
        payload = {"prompt": prompt, "duration": f"{int(duration_s)}s", "aspect_ratio": ratio}
        if image_url:
            payload["image_url"] = image_url
        out = _submit_and_wait(model, payload, timeout_s=600)
        url = out.get("video", {}).get("url")
        if not url:
            raise MediaError(f"{model} returned no video: {json.dumps(out)[:200]}")
        dest = _download(url, config.ASSET_DIR / f"{name}.mp4")

    logger.info("fal %s generated via %s — est $%.2f", kind, model, cost)
    return {"path": str(dest), "url": url, "model": model, "cost": cost, "seed": seed, "mock": False}
