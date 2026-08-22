"""Validated data contracts shared by indexing, search, and FastAPI."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class KBChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    corpus: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_tier: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=300_000)
    platform: list[str] = Field(default_factory=list)
    topic: list[str] = Field(default_factory=list)
    content_type: list[str] = Field(default_factory=list)
    claim_type: str = Field(min_length=1)
    quotes: list[Any] = Field(default_factory=list)
    stats: list[Any] = Field(default_factory=list)
    published: str | None = None
    retrieved_at: str
    hash: str = Field(min_length=1)

    @field_validator("platform", "topic", "content_type")
    @classmethod
    def normalize_metadata(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))

    @computed_field
    @property
    def source_id_full(self) -> str:
        return self.source_id if ":" in self.source_id else f"chunk:{self.source_id}"


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: list[str] | None = None
    topic: list[str] | None = None
    content_type: list[str] | None = None
    tier: list[str] | None = None
    claim_type: list[str] | None = None

    @field_validator("platform", "topic", "content_type", "tier", "claim_type")
    @classmethod
    def normalize_filters(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
        return normalized or None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4_000)
    k: int = Field(default=6, ge=1, le=50)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    corpus: str = Field(default="best_practice", min_length=1, max_length=100)
    rerank: bool = False

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query cannot be blank")
        return value


class ScoreComponents(BaseModel):
    dense: float
    bm25: float


class SearchResult(BaseModel):
    source_id: str
    tier: str
    title: str
    text: str
    claim_type: str
    stats: list[Any]
    score: float
    components: ScoreComponents


class Evidence(BaseModel):
    source_id: str
    title: str
    text: str
    source_url: str
    tier: str
    claim_type: str
    stats: list[Any] = Field(default_factory=list)


class ResolveSourceIdsRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("source_ids")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("at least one source id is required")
        if any(len(value) > 200 for value in cleaned):
            raise ValueError("source ids cannot exceed 200 characters")
        return list(dict.fromkeys(cleaned))


class ResolveSourceIdsResponse(BaseModel):
    resolved: list[str]
    unresolved: list[str]
