"""Retrieval tools — the ONLY tools registered for planning/feedback agents.

§11 guardrail: no network tools are registered in plotline-api, ever. Agents
see only what already landed in the DB, via the plotline-rag contract.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from app.rag_client import RoutingRag


TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "search_inspiration",
        "description": (
            "Search the annotated inspiration DB (curated high-performing posts/ads). "
            "Returns assets with source_id (asset:*), stats, hook analysis and a "
            "why-it-works note. Cite the source_id in your evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for (topic, hook style, angle)"},
                "niche": {"type": "string", "description": "Niche filter, e.g. ai_tools, skincare_d2c, fitness"},
                "platform": {"type": "string", "description": "Platform filter, e.g. instagram_reels, youtube_shorts, tiktok"},
                "format": {"type": "string", "description": "Format filter, e.g. listicle_demo, talking_head, ugc_testimonial"},
                "k": {"type": "integer", "description": "Max results (default 6)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_corpus",
        "description": (
            "Search the best-practice corpus (platform docs, retention/hook research, "
            "ad playbooks). Returns chunks with source_id (chunk:*) and a source tier. "
            "Cite the source_id in your evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "topic": {"type": "string", "description": "e.g. first_frame, retention_structure, cta, claims_safety"},
                "platform": {"type": "string"},
                "k": {"type": "integer", "description": "Max results (default 6)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_benchmarks",
        "description": (
            "Get computed niche benchmarks (median views/ER by format x niche) with n and "
            "as-of date. source_id is stat:*. Benchmarks with n<30 are never published."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "niche": {"type": "string"},
                "format": {"type": "string"},
                "platform": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "get_trends",
        "description": (
            "Get topic momentum (keyword velocity) and the curated trend/seasonal calendar. "
            "source_id is trend:*. Every entry is as-of stamped."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"niche": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "get_profile",
        "description": "Get the creator's profile memory: niche, tone rules, banned topics, capacity, style prefs.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


class ToolDispatcher:
    """Executes tool calls against the RAG service + local profile memory,
    and records every call + every source_id surfaced (for run logging)."""

    def __init__(
        self,
        rag: RoutingRag,
        get_profile: Callable[[], dict[str, Any]],
        surfaced_ids: Optional[set[str]] = None,
        platform_filter: Optional[list[str]] = None,
    ):
        self.rag = rag
        self._get_profile = get_profile
        self.calls: list[dict[str, Any]] = []
        # Shared across every agent in one pipeline run — the citation
        # allowlist for the local subset check (cite only what THIS run saw).
        self.surfaced_ids: set[str] = surfaced_ids if surfaced_ids is not None else set()
        # Addendum-01 §7.3: retrieval hard-filters platform — agents can narrow
        # within the context's platforms but never search outside them.
        self.platform_filter = platform_filter

    def dispatch(self, name: str, tool_input: dict[str, Any]) -> str:
        result = self._run(name, tool_input)
        self.calls.append({"tool": name, "input": tool_input, "result_count": _count(result)})
        for rec in _records(result):
            sid = rec.get("source_id")
            if sid:
                self.surfaced_ids.add(sid)
        return json.dumps(result, default=str)

    def _run(self, name: str, tool_input: dict[str, Any]) -> Any:
        if name == "search_inspiration":
            filters = {"kind": "asset"}
            for key in ("niche", "platform", "format"):
                if tool_input.get(key):
                    filters[key] = tool_input[key]
            if self.platform_filter:
                asked = tool_input.get("platform")
                # hard filter: narrow within context platforms, never outside
                filters["platform"] = asked if asked in self.platform_filter else self.platform_filter
            return self.rag.search_corpus(
                tool_input.get("query", ""), k=int(tool_input.get("k") or 6), filters=filters
            )
        if name == "search_corpus":
            filters = {"kind": "chunk"}
            for key in ("topic", "platform"):
                if tool_input.get(key):
                    filters[key] = tool_input[key]
            return self.rag.search_corpus(
                tool_input.get("query", ""), k=int(tool_input.get("k") or 6), filters=filters
            )
        if name == "get_benchmarks":
            filters = {"kind": "stat"}
            for key in ("niche", "format", "platform"):
                if tool_input.get(key):
                    filters[key] = tool_input[key]
            return self.rag.search_corpus("", k=12, filters=filters)
        if name == "get_trends":
            filters = {"kind": "trend"}
            if tool_input.get("niche"):
                filters["niche"] = tool_input["niche"]
            return self.rag.search_corpus("", k=12, filters=filters)
        if name == "get_profile":
            return self._get_profile()
        return {"error": f"unknown tool {name}"}


def _records(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    return []


def _count(result: Any) -> int:
    return len(_records(result)) if isinstance(result, list) else 1
