"""PixelBin media transport (primary provider, owner decision 2026-08-26).

The published SDK is Node; this service is Python, so the REST contract is
spoken directly. Paths and auth were read off `@pixelbin/admin` rather than
guessed:

    create  POST /service/platform/transformation/v1.0/predictions/{plugin}/{op}
            multipart/form-data · Authorization: Bearer <token> · → {_id}
    poll    GET  /service/platform/transformation/v1.0/predictions/{requestId}
            → {status: PENDING|SUCCESS|FAILURE, output: [url, ...]}

A prediction NAME is "<plugin>_<operation>" split on the FIRST underscore, so
`nanoBanana2_generate` → plugin `nanoBanana2`, op `generate`, and `erase_bg` →
plugin `erase`, op `bg`. Splitting on the last underscore would break the
second form, which is why this is a helper and not an inline `rsplit`.

This module raises `MediaError` and nothing else, so `media.py` can treat a
PixelBin failure and a fal failure identically when it decides to fall back.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx

from app import config

logger = logging.getLogger("plotline.media.pixelbin")

_PREDICT = "/service/platform/transformation/v1.0/predictions"


class PixelbinError(RuntimeError):
    """Transport or provider failure. `policy=True` → the model declined the
    prompt, which must never be retried against the same provider."""

    def __init__(self, message: str, policy: bool = False):
        self.policy = policy
        super().__init__(message)


def configured() -> bool:
    """True when a real PixelBin call is possible. Checked BEFORE routing so a
    missing token reads as 'not configured' rather than a runtime 401."""
    return bool(config.PIXELBIN_API_TOKEN.strip())


def split_name(name: str) -> tuple[str, str]:
    """'nanoBanana2_generate' → ('nanoBanana2', 'generate')."""
    if "_" not in name:
        raise PixelbinError(f"prediction name {name!r} is not '<plugin>_<operation>'")
    plugin, _, operation = name.partition("_")
    return plugin, operation


def _headers() -> dict[str, str]:
    if not configured():
        raise PixelbinError(
            "PIXELBIN_API_TOKEN is not set — PixelBin generation unavailable "
            "(set MOCK_MEDIA=1 to develop without it, or PLOTLINE_MEDIA_PROVIDER=fal)"
        )
    return {"Authorization": f"Bearer {config.PIXELBIN_API_TOKEN.strip()}"}


def _as_form(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Multipart fields. A list value is repeated under the same key, which is
    what the SDK does for `images` — a JSON-encoded array is silently ignored
    by the endpoint, so this shape is not cosmetic."""
    fields: list[tuple[str, str]] = []
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            fields.extend((key, str(v)) for v in value if v is not None)
        elif isinstance(value, bool):
            fields.append((key, "true" if value else "false"))
        else:
            fields.append((key, str(value)))
    return fields


def submit_and_wait(name: str, payload: dict[str, Any], timeout_s: float = 600) -> list[str]:
    """Create a prediction and poll to a terminal state. Returns output URLs.

    One bounded retry on transport/5xx, mirroring the fal client. A 4xx is a
    real answer from the server — never retried, because spinning on a rejected
    prompt burns wall-clock and tells the user nothing.
    """
    plugin, operation = split_name(name)
    url = f"{config.PIXELBIN_DOMAIN}{_PREDICT}/{plugin}/{operation}"
    last: Optional[Exception] = None

    for attempt in range(2):
        try:
            sub = httpx.post(url, headers=_headers(), files=[
                (k, (None, v)) for k, v in _as_form(payload)
            ], timeout=60)
            if sub.status_code in (400, 422):
                raise PixelbinError(f"{name} rejected the request: {sub.text[:300]}", policy=True)
            if sub.status_code in (402, 429):
                # quota and rate-limit are real answers; falling back to the
                # other provider is the right move, retrying here is not.
                raise PixelbinError(f"{name} refused: {sub.text[:200]}")
            sub.raise_for_status()
            job = sub.json()
            request_id = job.get("_id") or job.get("requestId") or job.get("id")
            if not request_id:
                raise PixelbinError(f"{name} returned no request id: {json.dumps(job)[:200]}")

            started = time.time()
            while time.time() - started < timeout_s:
                st = httpx.get(
                    f"{config.PIXELBIN_DOMAIN}{_PREDICT}/{request_id}",
                    headers=_headers(), timeout=30,
                ).json()
                status = str(st.get("status", "")).upper()
                if status == "SUCCESS":
                    out = st.get("output") or []
                    if isinstance(out, str):
                        out = [out]
                    urls = [u for u in out if isinstance(u, str) and u.startswith("http")]
                    if not urls:
                        raise PixelbinError(f"{name} succeeded with no output: {json.dumps(st)[:200]}")
                    return urls
                if status in ("FAILURE", "FAILED", "ERROR", "CANCELLED"):
                    detail = json.dumps(st)[:300]
                    low = detail.lower()
                    policy = any(w in low for w in ("content", "policy", "safety", "moderat"))
                    raise PixelbinError(f"{name} failed: {detail}", policy=policy)
                time.sleep(3)
            raise PixelbinError(f"{name} timed out after {timeout_s}s")

        except PixelbinError:
            raise
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last = exc
            logger.warning("pixelbin %s attempt %d failed (%s) — %s",
                           name, attempt + 1, type(exc).__name__, exc)
            if attempt == 0:
                time.sleep(1.5)
    raise PixelbinError(f"{name} unreachable after retry: {last}")


def generate(
    kind: str,
    prompt: str,
    *,
    ratio: str = "9:16",
    duration_s: float = 4.0,
    tier: str = "final",
    image_url: Optional[str] = None,
    resolution: str = "2K",
) -> dict[str, Any]:
    """One image or video via PixelBin. Returns {url, model}.

    Audio is deliberately absent: PixelBin's prediction catalogue has no TTS
    operation, so routing audio here would fail at the provider with a confusing
    message. `media.py` sends audio straight to fal and says so.
    """
    if kind == "image":
        key = "image_draft" if tier == "draft" else ("image_pro" if tier == "pro" else "image_final")
        model = config.PIXELBIN_MODELS[key]
        payload: dict[str, Any] = {
            "prompt": prompt,
            "aspect_ratio": ratio,
            "output_resolution": resolution,
        }
        if image_url:
            payload["images"] = [image_url]
        urls = submit_and_wait(model, payload, timeout_s=300)
    elif kind == "video":
        model = config.PIXELBIN_MODELS["video"]
        payload = {
            "prompt": prompt,
            "aspect_ratio": ratio,
            "duration": int(duration_s),
        }
        if image_url:
            payload["images"] = [image_url]
        urls = submit_and_wait(model, payload, timeout_s=900)
    else:
        raise PixelbinError(f"PixelBin has no {kind} operation — fal handles that kind")

    logger.info("pixelbin %s generated via %s", kind, model)
    return {"url": urls[0], "model": f"pixelbin:{model}"}
