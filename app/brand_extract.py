"""Brand-URL extractor — a SYSTEM pipeline, never an agent tool (v2 §Step 2).

Fetches the page and pulls palette / font / logo / tagline with plain parsing.
The user confirms or edits every value in the Brand card; nothing here is
authoritative until they do. No LLM call, no invention: a field we can't find
comes back empty rather than guessed.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger("plotline.brand")

_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;}\"']+)", re.IGNORECASE)
_GENERIC_FONTS = {"sans-serif", "serif", "monospace", "inherit", "system-ui", "-apple-system", "cursive"}

_UA = {"User-Agent": "Mozilla/5.0 (compatible; PlotlineBrandFetch/1.0)"}
_MAX_REDIRECTS = 5
_NOT_PUBLIC_NOTE = "that URL isn't publicly reachable — fill the fields manually"


class _NotPublic(httpx.HTTPError):
    """Refused before any socket work. Subclasses HTTPError so the optional
    stylesheet fetches skip it exactly like a network failure."""


def _is_public(url: str) -> bool:
    """http(s) only, and every address the host resolves to must be public.
    The fetch happens on the server, so an internal host would turn a campaign
    into an SSRF probe (169.254.169.254, localhost, 10/8, ::1)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return bool(infos)


def _fetch(url: str, timeout: float) -> httpx.Response:
    """GET with the redirect chain re-checked hop by hop — follow_redirects
    would let a public URL bounce the fetch onto a private one."""
    for _ in range(_MAX_REDIRECTS + 1):
        if not _is_public(url):
            raise _NotPublic(_NOT_PUBLIC_NOTE)
        response = httpx.get(url, timeout=timeout, follow_redirects=False, headers=_UA)
        if response.is_redirect:
            url = urljoin(url, response.headers["location"])
            continue
        response.raise_for_status()
        return response
    raise _NotPublic(_NOT_PUBLIC_NOTE)  # redirect loop


def _meta(html: str, *names: str) -> str | None:
    for name in names:
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)',
            html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m = re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
            html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def extract(url: str, timeout: float = 12.0) -> dict[str, Any]:
    """Returns {palette, font, logo_url, tagline, source_url, notes[]} —
    every field best-effort, empty when not found (never invented)."""
    out: dict[str, Any] = {"palette": [], "font": None, "logo_url": None,
                           "tagline": None, "source_url": url, "notes": []}
    if "://" not in url:
        url = "https://" + url
        out["source_url"] = url
    try:
        response = _fetch(url, timeout)
        html = response.text
    except _NotPublic:
        out["notes"].append(_NOT_PUBLIC_NOTE)
        return out
    except httpx.HTTPError as exc:
        out["notes"].append(f"couldn't fetch the page ({type(exc).__name__}) — fill the fields manually")
        return out
    base = str(response.url)  # relative assets hang off where we LANDED, not where we asked

    # palette: most common non-greyscale hex colors in inline styles/CSS links
    css = html
    for href in re.findall(r'<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\']([^"\']+)', html, re.IGNORECASE)[:3]:
        try:
            css += _fetch(urljoin(base, href), timeout).text
        except httpx.HTTPError:
            continue
    counts: Counter[str] = Counter()
    for hexcode in _HEX.findall(css):
        h = hexcode.lower()
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r_, g_, b_ = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        if max(r_, g_, b_) - min(r_, g_, b_) < 12:  # skip greys/near-greys
            continue
        counts[f"#{h.upper()}"] += 1
    out["palette"] = [c for c, _ in counts.most_common(6)]

    fonts = []
    for stack in _FONT_FAMILY.findall(css)[:60]:
        first = stack.split(",")[0].strip().strip("\"'")
        if first and first.lower() not in _GENERIC_FONTS and not first.startswith("var("):
            fonts.append(first)
    if fonts:
        out["font"] = Counter(fonts).most_common(1)[0][0]

    logo = _meta(html, "og:image", "twitter:image")
    if not logo:
        m = re.search(r'<img[^>]+(?:class|id|alt)=["\'][^"\']*logo[^"\']*["\'][^>]+src=["\']([^"\']+)', html, re.IGNORECASE)
        logo = m.group(1) if m else None
    if logo:
        out["logo_url"] = urljoin(base, logo)

    out["tagline"] = _meta(html, "og:description", "description", "twitter:description")

    missing = [k for k in ("palette", "font", "logo_url", "tagline") if not out[k]]
    if missing:
        out["notes"].append(f"couldn't find: {', '.join(missing)} — add them yourself")
    logger.info("brand extract %s → palette=%d font=%s logo=%s",
                url, len(out["palette"]), bool(out["font"]), bool(out["logo_url"]))
    return out


_CLAIM_CUE = re.compile(
    r"\b(\d+ ?%|\d+x|clinically|proven|guarantee\w*|certified|approved|award|"
    r"first|only|fastest|cheapest|save[sd]? [\w$₹]+|reduce[sd]? \d+|up to \d+)", re.IGNORECASE)
_BAN_CUE = re.compile(
    r"\b(?:never|do not|don't|avoid|prohibited|banned|must not)\s+(?:say|use|claim|write)?\s*[:\-]?\s*([^.\n;]{3,60})",
    re.IGNORECASE)


def extract_claims(policy_text: str, product_description: str) -> dict[str, list[str]]:
    """Candidate approved claims + banned words from the policy doc and product
    description. CANDIDATES ONLY — the user one-tap confirms in the Brand card;
    the confirmed list is the claims source of truth."""
    claims: list[str] = []
    for source in (product_description or "", policy_text or ""):
        for sentence in re.split(r"[.\n;]", source):
            s = sentence.strip()
            if 12 <= len(s) <= 160 and _CLAIM_CUE.search(s):
                claims.append(s)
    banned = [m.strip(" '\"") for m in _BAN_CUE.findall(policy_text or "")]
    dedupe = lambda xs: list(dict.fromkeys(x for x in xs if x))
    return {"approved_claims": dedupe(claims)[:12], "banned_words": dedupe(banned)[:12]}
