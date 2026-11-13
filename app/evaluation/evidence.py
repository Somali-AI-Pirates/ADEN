"""The evidence actually delivered to the answer, with stable local labels."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any


def chunk_text(chunk: Mapping[str, Any]) -> str:
    text = chunk.get("text") or ""
    heading = chunk.get("heading")
    return f"제목: {heading}\n{text}" if heading else text


def sql_evidence(record: Mapping[str, Any]) -> dict[str, str]:
    return {
        f"SQL:{index}": "SQL 조회 결과\n"
        + json.dumps(result, ensure_ascii=False, sort_keys=True)
        for index, result in enumerate(record.get("included_sql_results") or [], 1)
    }


def final_evidence(record: Mapping[str, Any]) -> dict[str, str]:
    """A reference answer or an attempted-but-excluded SQL result is never evidence."""
    result: dict[str, str] = {}
    for index, chunk in enumerate(record.get("final_context_chunks") or [], 1):
        source_id = chunk.get("source_id") or chunk.get("chunk_id") or f"context:{index}"
        text = chunk_text(chunk)
        if source_id in result:
            result[source_id] += "\n\n" + text
        else:
            result[source_id] = text
    result.update(sql_evidence(record))
    return result


def labeled_contexts(record: Mapping[str, Any]) -> list[str]:
    return [f"[{source_id}]\n{text}" for source_id, text in final_evidence(record).items()]


def normalized_quote(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def quote_exists(quote: str, text: str) -> bool:
    normalized = normalized_quote(quote)
    return bool(normalized) and normalized in normalized_quote(text)
