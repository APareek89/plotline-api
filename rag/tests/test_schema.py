from __future__ import annotations

import pytest
from pydantic import ValidationError

from plotline_rag.schema import KBChunk, SearchFilters, SearchRequest

from conftest import chunk_dict


def test_chunk_expands_resolvable_source_id() -> None:
    chunk = KBChunk.model_validate(chunk_dict("C0042", "A useful evidence chunk."))
    assert chunk.source_id_full == "chunk:C0042"


def test_full_source_id_is_preserved() -> None:
    payload = chunk_dict("chunk:C0042", "A useful evidence chunk.")
    chunk = KBChunk.model_validate(payload)
    assert chunk.source_id_full == "chunk:C0042"


def test_search_request_normalizes_filters() -> None:
    request = SearchRequest(
        query="  retention guidance  ",
        filters=SearchFilters(platform=["YouTube", "youtube"], tier=["Official"]),
    )
    assert request.query == "retention guidance"
    assert request.filters.platform == ["youtube"]
    assert request.filters.tier == ["official"]


def test_blank_query_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchRequest(query="   ")

