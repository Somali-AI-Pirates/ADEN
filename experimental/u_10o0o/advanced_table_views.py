"""Render compact, source-linked views for relevant curated table rows.

This is an evaluation adapter over the local curation output. It does not infer
new facts, join rows, or make any external model calls. A rendered view represents
one reviewed row from one source page; weak matches keep the original page.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import tiktoken

_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9]{1,12}(?:-[A-Za-z0-9]{1,12}){1,4}(?![A-Za-z0-9_-])"
)
_PARTS = re.compile(r"[가-힣]+|[a-z0-9]+")


@lru_cache(maxsize=4096)
def _tokens(text: str) -> frozenset[str]:
    """Use the experiment's bigram-plus-identifier vocabulary for row matching."""
    tokens: set[str] = set()
    for part in _PARTS.findall(text.lower()):
        if part.isascii() or len(part) == 1:
            tokens.add(part)
        else:
            tokens.update(part[index : index + 2] for index in range(len(part) - 1))
    tokens.update(match.lower() for match in _IDENTIFIER.findall(text))
    return frozenset(tokens)


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("o200k_base")


@lru_cache(maxsize=4096)
def _token_count(text: str) -> int:
    return len(_encoding().encode(text)) if text else 0


def _positive_page(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    return page if page > 0 else None


class TableViews:
    """Create one-row evidence views while retaining the PDF page as provenance.

    `curated_dir` must be a local curation snapshot containing `sources.jsonl`,
    `rules.jsonl`, and `records/*.jsonl`. `render` preserves input order and page
    scores. For each ranked page, it either substitutes the best sufficiently
    relevant reviewed row or returns that page unchanged.
    """

    def __init__(self, curated_dir: str | Path):
        self.curated_dir = Path(curated_dir)
        source_path = self.curated_dir / "sources.jsonl"
        rule_path = self.curated_dir / "rules.jsonl"
        records_dir = self.curated_dir / "records"
        missing = [
            str(path)
            for path in (source_path, rule_path, records_dir)
            if not path.exists()
        ]
        if missing:
            raise FileNotFoundError(
                "Local curation snapshot is missing required paths: "
                + ", ".join(missing)
            )

        source_rows = self._read_jsonl(source_path)
        self.sources = {
            row["source_id"]: row
            for row in source_rows
            if isinstance(row.get("source_id"), str)
        }
        self.rules = self._read_jsonl(rule_path)
        self.rows_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.rules_by_id = {
            row["rule_id"]: row
            for row in self.rules
            if isinstance(row.get("rule_id"), str)
        }

        for path in sorted(records_dir.glob("*.jsonl")):
            for row in self._read_jsonl(path):
                if (
                    row.get("kind") == "table_row"
                    and row.get("review_status") == "parsed"
                    and isinstance(row.get("source_id"), str)
                    and isinstance(row.get("fields"), dict)
                    and row["fields"]
                ):
                    self.rows_by_source[row["source_id"]].append(row)

        self._row_count = sum(map(len, self.rows_by_source.values()))
        self._row_document_frequency: Counter[str] = Counter()
        for rows in self.rows_by_source.values():
            for row in rows:
                self._row_document_frequency.update(_tokens(self._searchable_row(row)))

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows = []
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"Expected a JSON object in {path}:{line_number}.")
                rows.append(row)
        return rows

    @staticmethod
    def _searchable_row(row: dict[str, Any]) -> str:
        fields = row.get("fields", {})
        return " ".join(
            [
                str(row.get("text", "")),
                *(f"{key} {value}" for key, value in fields.items()),
            ]
        )

    def _idf(self, token: str) -> float:
        # Global document frequency suppresses ubiquitous table labels. We still
        # score against the rows on this source page only.
        total = max(self._row_count, 1)
        frequency = self._row_document_frequency[token]
        return math.log1p((total - frequency + 0.5) / (frequency + 0.5))

    def _match_score(
        self,
        question_tokens: frozenset[str],
        question: str,
        row: dict[str, Any],
    ) -> tuple[float, int, bool]:
        row_text = self._searchable_row(row)
        row_tokens = _tokens(row_text)
        question_ids = {match.lower() for match in _IDENTIFIER.findall(question)}
        identifier_tokens = set().union(*(_tokens(value) for value in question_ids))
        content_question_tokens = question_tokens - identifier_tokens
        matches = content_question_tokens & row_tokens

        # Only identifiers in this row's cells count as a direct row match.
        row_ids = {value.lower() for value in _IDENTIFIER.findall(row_text)}
        exact_identifier = bool(question_ids & row_ids)
        if not matches:
            return 0.0, 0, exact_identifier

        total_weight = sum(self._idf(token) for token in content_question_tokens)
        matched_weight = sum(self._idf(token) for token in matches)
        coverage = matched_weight / total_weight if total_weight else 0.0

        return coverage, len(matches), exact_identifier

    @staticmethod
    def _field_order(fields: dict[str, Any], page_text: str) -> list[tuple[str, str]]:
        items = [
            (str(key), str(value)) for key, value in fields.items() if value is not None
        ]

        def order(item: tuple[str, str]) -> tuple[int, str]:
            index = page_text.find(item[0]) if page_text else -1
            return (index if index >= 0 else 2**31, item[0])

        return sorted(items, key=order)

    def _best_rule(
        self,
        question_tokens: frozenset[str],
        row: dict[str, Any],
        source_id: str,
    ) -> dict[str, Any] | None:
        candidates = []
        for rule_id in row.get("context_rule_ids", []):
            rule = self.rules_by_id.get(rule_id)
            if not rule or source_id not in rule.get("sources", []):
                continue
            text = rule.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            matches = question_tokens & _tokens(text)
            if len(matches) < 2:
                continue
            weight = sum(self._idf(token) for token in matches)
            candidates.append((weight, rule))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def _render_row(
        self,
        page: dict[str, Any],
        source: dict[str, Any],
        row: dict[str, Any],
        rule: dict[str, Any] | None,
    ) -> dict[str, Any]:
        page_text = page.get("text", "")
        if not isinstance(page_text, str):
            return page
        fields = self._field_order(row["fields"], page_text)
        lines = ["표 행 근거"]
        heading = source.get("heading")
        section = row.get("section")
        if isinstance(heading, str) and heading.strip():
            lines.append(f"문서 문맥: {heading.strip()}")
        if isinstance(section, str) and section.strip() and section.strip() != heading:
            lines.append(f"표 구역: {section.strip()}")

        original_row = row.get("text")
        if isinstance(original_row, str) and original_row.strip():
            lines.append(f"원문 행: {original_row.strip()}")
        cells = [f"{key}: {value.strip()}" for key, value in fields if value.strip()]
        if cells:
            lines.append("열과 값: " + " | ".join(cells))
        if rule:
            lines.append(f"같은 원문 쪽의 관련 조건: {rule['text'].strip()}")

        rendered_text = "\n".join(lines)
        if _token_count(rendered_text) >= _token_count(str(page_text)):
            return page

        result = dict(page)
        result["text"] = rendered_text
        result["heading"] = ""
        result["chunk_id"] = f"table-view:{row['record_id']}"
        result["table_view_record_id"] = row["record_id"]
        result["evidence_view"] = "table_row"
        return result

    def render(
        self,
        question: str,
        ranked: list[tuple[dict[str, Any], float]],
    ) -> list[tuple[dict[str, Any], float]]:
        """Replace a ranked page with its strongest relevant single-row view.

        The page's filename and page number remain unchanged. Rows from separate
        cases or table sections are never joined. If the query has weak overlap,
        source metadata cannot be verified, or the view does not reduce tokens,
        the original page item is returned unchanged.
        """
        if not isinstance(question, str) or not question.strip():
            return list(ranked)
        question_tokens = _tokens(question)
        if not question_tokens:
            return list(ranked)

        rendered: list[tuple[dict[str, Any], float]] = []
        for page, score in ranked:
            if not isinstance(page, dict):
                rendered.append((page, score))
                continue
            file_name = page.get("file_name")
            page_number = _positive_page(page.get("page"))
            if (
                not isinstance(file_name, str)
                or not file_name.strip()
                or page_number is None
            ):
                rendered.append((page, score))
                continue

            book_id = str(page.get("book_id") or Path(file_name).stem)
            source_id = f"advanced:{book_id}:p{page_number}"
            source = self.sources.get(source_id)
            if (
                not source
                or source.get("book_id") != book_id
                or source.get("file_name") != file_name
                or source.get("page") != page_number
            ):
                rendered.append((page, score))
                continue

            rows = self.rows_by_source.get(source_id, [])
            if not rows:
                rendered.append((page, score))
                continue

            query_identifiers = {
                value.lower() for value in _IDENTIFIER.findall(question)
            }
            identifier_frequency: Counter[str] = Counter()
            for row in rows:
                identifier_frequency.update(
                    {
                        value.lower()
                        for value in _IDENTIFIER.findall(self._searchable_row(row))
                    }
                )
            unique_query_identifiers = {
                value for value in query_identifiers if identifier_frequency[value] == 1
            }

            scored = []
            for row in rows:
                coverage, match_count, exact_identifier = self._match_score(
                    question_tokens, question, row
                )
                row_identifiers = {
                    value.lower()
                    for value in _IDENTIFIER.findall(self._searchable_row(row))
                }
                unique_identifier = exact_identifier and bool(
                    row_identifiers & unique_query_identifiers
                )
                # A unique row-cell identifier is specific enough. Other
                # candidates need several distinct non-ID terms and meaningful
                # coverage; repeated IDs cannot choose an arbitrary sibling row.
                reliable = unique_identifier or (match_count >= 2 and coverage >= 0.12)
                if reliable:
                    scored.append((coverage, match_count, unique_identifier, row))
            if not scored:
                rendered.append((page, score))
                continue

            _, _, _, best_row = max(
                scored,
                key=lambda item: (item[2], item[0], item[1], item[3]["record_id"]),
            )
            rule = self._best_rule(question_tokens, best_row, source_id)
            view = self._render_row(page, source, best_row, rule)
            rendered.append((view, score))

        return rendered
