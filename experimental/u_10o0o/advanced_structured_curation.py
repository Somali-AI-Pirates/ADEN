"""Verified IS table rendering and HG repeated-rule source experiments.

This module builds local, immutable experiment inputs and compares them with
the existing Advanced retrieval path. It reads source-derived curation only;
evaluation references are loaded after retrieval results have been saved.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from time import perf_counter
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping, Sequence

import numpy as np

from app.ingestion.curation import read_rows
from app.ingestion.layout import (
    blocks,
    compact,
    header_cells,
    looks_like_row,
    parse_table,
    readable,
)
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES, build_index, hg_ids
from experimental.u_10o0o.advanced_snapshots import (
    ARTIFACTS,
    IS_HEADING,
    IS_SNAPSHOT,
    _page_family,
    _page_snapshot_sha,
    _read_pages_and_sources,
    _sha_bytes,
    _sha_file,
    load_is,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    DEFAULT_CONTEXT_TOKENS,
    OFFICIAL_SPLITS,
    evaluation_files,
    evaluation_questions,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_experiment_eval import (
    Search,
    batches,
    finish,
    score as evaluation_score,
    stamp,
    trace,
    write_json,
)
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX, augment, location
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_source_lookup import apply_sources
from experimental.u_10o0o.advanced_clause_binding import roles_in
from experimental.u_10o0o.embeding import (
    BATCH_SIZE,
    EMBEDDING_DIM,
    MODEL_ID,
    MODEL_REVISION,
    VECTORS_PATH,
    load_model,
)
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT
from experimental.u_10o0o.bm25 import BM25Index, tokenize_bigram_id
from experimental.u_10o0o.search import load_index

IS_TABLE_SNAPSHOT = ARTIFACTS / "advanced-snapshots/is-table-rendered-v1"
HG_RULE_SNAPSHOT = ARTIFACTS / "advanced-snapshots/hg-rule-occurrences-v1"
LAYOUT_CACHE = PROJECT_ROOT / "artifacts/u_10o0o/pattern-layout-cache/advanced"
IS_TABLE_TYPE = "is-table-rendered-vectors"
HG_RULE_TYPE = "hg-repeated-rule-occurrences-sqlite"
SCHEMA_VERSION = 1
PAGE_FOOTER = re.compile(r"\d+\s*/\s*\d+")
PARAGRAPH = re.compile(r"\n\s*\n+")


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("wb") as stream:
        for row in rows:
            stream.write(_canonical_bytes(row))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _verified_layout_pages(curated_manifest: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    documents = {
        item["file_name"]: item
        for item in curated_manifest["identity"]["documents"]
    }
    books = sorted({source["book_id"] for source in sources.values() if IS_HEADING.match(source["heading"])})
    result = {}
    for book_id in books:
        book_sources = [
            source
            for source in sources.values()
            if source["book_id"] == book_id and IS_HEADING.match(source["heading"])
        ]
        document = documents[book_sources[0]["file_name"]]
        path = LAYOUT_CACHE / f"{book_id}-{document['sha256'][:16]}.txt"
        meta_path = path.with_suffix(".json")
        if not path.is_file() or not meta_path.is_file():
            raise FileNotFoundError("검증된 IS 원본 레이아웃 캐시가 없습니다.")
        meta = json.loads(meta_path.read_bytes())
        raw = path.read_bytes()
        if (
            meta.get("identity", {}).get("pdf_sha256") != document["sha256"]
            or meta.get("identity", {}).get("extractor") != curated_manifest["identity"]["extractor"]
            or meta.get("identity", {}).get("mode") != "layout"
            or meta.get("text_sha256") != _sha_bytes(raw)
        ):
            raise ValueError("IS 원본 레이아웃 캐시의 PDF·추출기 지문이 다릅니다.")
        raw_pages = raw.decode("utf-8").split("\f")
        if raw_pages and not raw_pages[-1].strip():
            raw_pages.pop()
        if len(raw_pages) != document["pages"]:
            raise ValueError("IS 원본 레이아웃 캐시의 쪽 수가 PDF와 다릅니다.")
        for source in book_sources:
            result[source["source_id"]] = raw_pages[source["page"] - 1]
    if len(result) != 3_750:
        raise ValueError("검증된 IS 원본 레이아웃에서 3,750쪽을 확인하지 못했습니다.")
    return result


def _published(output: Path, snapshot_type: str, inputs: Mapping[str, Any]) -> bool:
    manifest_path = output / "manifest.json"
    if not output.exists():
        return False
    if not manifest_path.is_file():
        raise FileExistsError("기존 실험 스냅샷을 덮어쓰지 않습니다.")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("inputs") != inputs or manifest.get("snapshot_type") != snapshot_type:
        raise FileExistsError("같은 경로에 다른 입력의 실험 스냅샷이 있습니다.")
    for name, digest in manifest.get("outputs", {}).items():
        if _sha_file(output / name) != digest:
            raise ValueError("기존 실험 스냅샷 파일이 manifest와 다릅니다.")
    return True


def _publish(output: Path, staging: Path, snapshot_type: str, inputs: Mapping[str, Any], config: Mapping[str, Any], counts: Mapping[str, Any]) -> Path:
    outputs = {
        path.name: _sha_file(path)
        for path in staging.iterdir()
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_type": snapshot_type,
        "inputs": dict(inputs),
        "outputs": outputs,
        "config": dict(config),
        "counts": dict(counts),
        "snapshot_id": _sha_bytes(_canonical_bytes({"inputs": inputs, "outputs": outputs})),
    }
    (staging / "manifest.json").write_bytes(_canonical_bytes(manifest))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if _published(output, snapshot_type, inputs):
            return output
        raise FileExistsError("기존 실험 스냅샷을 덮어쓰지 않습니다.")
    os.replace(staging, output)
    return output


def _render_verified_tables(
    page: Mapping[str, Any],
    source: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    base_input: Mapping[str, Any],
    layout_text: str,
) -> tuple[str, dict[str, Any]]:
    """Render only parsed IS tables whose ordered headers and source rows agree."""
    source_id = source["source_id"]
    if not IS_HEADING.match(source.get("heading", "")):
        return str(base_input["text"]), {"status": "not_is", "tables": 0, "rows": 0}

    table_records = {
        (row["line_start"], row["line_end"]): row
        for row in records
        if row.get("source_id") == source_id and row.get("kind") == "table_row"
    }
    if not table_records:
        return str(base_input["text"]), {"status": "no_verified_table", "tables": 0, "rows": 0}
    removed = base_input.get("removed_blocks", [])
    original_blocks = [part for part in blocks(page["text"]) if part["lines"]]
    if (
        len(removed) != 3
        or removed[:2] != [0, 1]
        or removed[-1] != len(original_blocks) - 1
        or not PAGE_FOOTER.fullmatch(original_blocks[-1]["lines"][-1].strip())
    ):
        return str(base_input["text"]), {"status": "uncertain_page_boundary", "tables": 0, "rows": 0}

    replacements: list[tuple[int, int, str]] = []
    observed_records: set[tuple[int, int]] = set()
    table_count = row_count = 0
    layout_parts = [part for part in blocks(layout_text) if part["lines"]]
    base_text = str(base_input["text"])
    compact_chars = []
    compact_positions = []
    for position, character in enumerate(base_text):
        if not character.isspace():
            compact_chars.append(character)
            compact_positions.append(position)
    compact_base = "".join(compact_chars)
    index = 0
    while index < len(layout_parts):
        header = layout_parts[index]
        cells = header_cells(header)
        if cells is None:
            index += 1
            continue
        end = index + 1
        while end < len(layout_parts) and looks_like_row(layout_parts[end]) and not header_cells(layout_parts[end]):
            end += 1
        if end == index + 1:
            index += 1
            continue
        rows = layout_parts[index + 1 : end]
        parsed = parse_table(header, rows)
        row_keys = [(row["start"], row["end"]) for row in rows]
        has_curated_rows = any(key in table_records for key in row_keys)
        if not has_curated_rows:
            index = end
            continue
        if parsed is None or any(key not in table_records for key in row_keys):
            return str(base_input["text"]), {"status": "table_parse_mismatch", "tables": 0, "rows": 0}
        labels = [readable(label) for _, _, label in cells]
        rendered_rows = []
        section = None
        for span, values in zip(row_keys, parsed, strict=True):
            record = table_records[span]
            stored = record.get("fields", {})
            if (
                record.get("review_status") != "parsed"
                or set(stored) != set(labels)
                or any(stored.get(label) != values.get(label) for label in labels)
            ):
                return str(base_input["text"]), {"status": "table_record_mismatch", "tables": 0, "rows": 0}
            row_section = record.get("section", "").strip()
            if not row_section or (section is not None and row_section != section):
                return str(base_input["text"]), {"status": "table_section_mismatch", "tables": 0, "rows": 0}
            section = row_section
            rendered_rows.append(" | ".join(f"{label}: {values[label]}" for label in labels))
            observed_records.add(span)
        layout_lines = layout_text.splitlines()
        raw_table = "\n".join(layout_lines[header["start"] - 1 : rows[-1]["end"]])
        needle = compact(raw_table)
        matches = []
        cursor = 0
        while needle and (found := compact_base.find(needle, cursor)) >= 0:
            matches.append(found)
            cursor = found + 1
        if len(matches) != 1:
            return str(base_input["text"]), {"status": "source_text_not_unique", "tables": 0, "rows": 0}
        compact_start = matches[0]
        compact_end = compact_start + len(needle) - 1
        replacement = f"{section}\n" + "\n".join(rendered_rows)
        replacements.append(
            (compact_positions[compact_start], compact_positions[compact_end] + 1, replacement)
        )
        table_count += 1
        row_count += len(rows)
        index = end

    if observed_records != set(table_records):
        return str(base_input["text"]), {"status": "unmatched_table_records", "tables": 0, "rows": 0}
    if not replacements:
        return str(base_input["text"]), {"status": "no_layout_table", "tables": 0, "rows": 0}

    for previous, following in zip(sorted(replacements), sorted(replacements)[1:]):
        if previous[1] > following[0]:
            return str(base_input["text"]), {"status": "overlapping_layout_tables", "tables": 0, "rows": 0}
    text = base_text
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    heading = source["heading"].strip()
    if heading and heading not in text:
        text = f"{heading}\n\n{text}"
    if not text.strip():
        return str(base_input["text"]), {"status": "empty_rendered_body", "tables": 0, "rows": 0}
    return text, {"status": "rendered", "tables": table_count, "rows": row_count}


def build_is_table_snapshot(
    output_root: Path = IS_TABLE_SNAPSHOT,
    *,
    base_snapshot: Path = IS_SNAPSHOT,
    device: str = "cuda",
) -> Path:
    """Build table-labeled IS vectors over the verified common-block snapshot."""
    from experimental.u_10o0o.advanced_snapshots import _read_jsonl

    pages, source_by_id = _read_pages_and_sources()
    base_vectors = np.load(VECTORS_PATH, mmap_mode="r", allow_pickle=False)
    common_vectors = load_is(base_snapshot, pages, np.asarray(base_vectors))
    base_manifest = json.loads((base_snapshot / "manifest.json").read_bytes())
    base_inputs = {row["source_id"]: row for row in _read_jsonl(base_snapshot / "page_inputs.jsonl")}
    page_ids = [f"advanced:{Path(page['file_name']).stem}:p{page['page']}" for page in pages]
    if set(base_inputs) != set(page_ids):
        raise ValueError("기존 IS 스냅샷의 입력 출처가 전체 페이지와 다릅니다.")

    curated_manifest_path = CURATED / "manifest.json"
    curated_manifest = json.loads(curated_manifest_path.read_bytes())
    layout_pages = _verified_layout_pages(curated_manifest, source_by_id)
    is_sources = {source_id: source for source_id, source in source_by_id.items() if IS_HEADING.match(source["heading"])}
    table_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for book_id in sorted({source["book_id"] for source in is_sources.values()}):
        for row in read_rows(CURATED / "records" / f"{book_id}.jsonl"):
            if row.get("source_id") in is_sources and row.get("kind") == "table_row":
                table_records[row["source_id"]].append(row)

    page_by_id = dict(zip(page_ids, pages, strict=True))
    page_inputs: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    changed_ids: list[str] = []
    status_counts = Counter()
    for source_id in page_ids:
        base_row = base_inputs[source_id]
        page = page_by_id[source_id]
        source = source_by_id[source_id]
        if source_id in is_sources:
            text, details = _render_verified_tables(
                page,
                source,
                table_records.get(source_id, ()),
                base_row,
                layout_pages[source_id],
            )
            status_counts[details["status"]] += 1
            ledger.append({"source_id": source_id, **details, "input_sha256": _sha_bytes(text.encode())})
        else:
            text = base_row["text"]
        row = {
            "source_id": source_id,
            "text": text,
            "input_sha256": _sha_bytes(text.encode("utf-8")),
            "removed_blocks": base_row.get("removed_blocks", []),
            "rendered_tables": next((item["tables"] for item in ledger[-1:] if item["source_id"] == source_id), 0),
        }
        page_inputs.append(row)
        if text != base_row["text"]:
            changed_ids.append(source_id)
    if len(page_inputs) != 18_000 or len(ledger) != 3_750 or not changed_ids:
        raise ValueError("IS table 변환의 출처 수 또는 변경 대상이 예상과 다릅니다.")

    base_sha = _sha_bytes(np.ascontiguousarray(base_vectors).tobytes())
    inputs = {
        "page_snapshot_sha256": _page_snapshot_sha(pages),
        "curation_manifest_sha256": _sha_file(curated_manifest_path),
        "curation_records_sha256": {
            name: digest
            for name, digest in curated_manifest["outputs"].items()
            if name.startswith("records/") and name.split("/", 1)[1].split(".", 1)[0] in {source["book_id"] for source in is_sources.values()}
        },
        "base_vectors_content_sha256": base_sha,
        "base_snapshot_id": base_manifest["snapshot_id"],
        "model": MODEL_ID,
        "model_revision": MODEL_REVISION,
    }
    output_root = Path(output_root)
    if _published(output_root, IS_TABLE_TYPE, inputs):
        return output_root

    vectors = np.array(common_vectors, dtype=np.float32, copy=True)
    indices = {source_id: index for index, source_id in enumerate(page_ids)}
    page_input_by_id = {row["source_id"]: row["text"] for row in page_inputs}
    texts = [page_input_by_id[source_id] for source_id in changed_ids]
    model = load_model(device)
    token_lengths = [
        len(tokens)
        for tokens in model.tokenizer(texts, truncation=False, verbose=False)["input_ids"]
    ]
    if model.max_seq_length is None or any(length > model.max_seq_length for length in token_lengths):
        raise ValueError("렌더된 IS 표 입력이 임베딩 모델의 토큰 한도를 넘습니다.")
    embedded = model.encode_document(
        texts,
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    if embedded.shape != (len(changed_ids), EMBEDDING_DIM):
        raise ValueError("새 IS 표 벡터의 행 수 또는 차원이 맞지 않습니다.")
    for source_id, vector in zip(changed_ids, embedded, strict=True):
        vectors[indices[source_id]] = vector
    del model, embedded
    norms = np.linalg.norm(vectors, axis=1)
    if not np.isfinite(vectors).all() or not np.allclose(norms, 1, atol=2e-3):
        raise ValueError("IS 표 벡터가 유한하지 않거나 정규화되지 않았습니다.")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=output_root.parent, prefix=f".{output_root.name}-"))
    try:
        _write_jsonl(staging / "source_ids.jsonl", [{"source_id": source_id} for source_id in page_ids])
        _write_jsonl(staging / "page_inputs.jsonl", page_inputs)
        _write_jsonl(staging / "render_ledger.jsonl", ledger)
        np.save(staging / "vectors.npy", vectors, allow_pickle=False)
        _publish(
            output_root,
            staging,
            IS_TABLE_TYPE,
            inputs,
            {
                "model": MODEL_ID,
                "revision": MODEL_REVISION,
                "dimension": EMBEDDING_DIM,
                "normalized": True,
                "device": device,
                "page_order": "Advanced restored page cache order",
                "baseline": "verified common-block IS snapshot",
            },
            {
                "pages": len(page_ids),
                "is_pages": len(ledger),
                "changed_vectors": len(changed_ids),
                "render_status": dict(status_counts),
                "rendered_tables": sum(row["tables"] for row in ledger),
                "rendered_rows": sum(row["rows"] for row in ledger),
                "max_input_tokens": max(token_lengths, default=0),
                "over_model_limit": 0,
            },
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output_root


def load_is_table_vectors(snapshot: Path, pages: Sequence[dict[str, Any]], base_vectors: np.ndarray) -> np.ndarray:
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot / "manifest.json").read_bytes())
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("snapshot_type") != IS_TABLE_TYPE:
        raise ValueError("IS 표 스냅샷 버전이 다릅니다.")
    if manifest["inputs"].get("page_snapshot_sha256") != _page_snapshot_sha(pages):
        raise ValueError("IS 표 스냅샷의 원본 페이지 지문이 다릅니다.")
    base_sha = _sha_bytes(np.ascontiguousarray(base_vectors).tobytes())
    if manifest["inputs"].get("base_vectors_content_sha256") != base_sha:
        raise ValueError("IS 표 스냅샷의 기준 벡터 지문이 다릅니다.")
    if manifest.get("snapshot_id") != _sha_bytes(_canonical_bytes({"inputs": manifest["inputs"], "outputs": manifest["outputs"]})):
        raise ValueError("IS 표 스냅샷 manifest 지문이 다릅니다.")
    for name, digest in manifest.get("outputs", {}).items():
        if _sha_file(snapshot / name) != digest:
            raise ValueError("IS 표 스냅샷 출력 파일 지문이 다릅니다.")
    page_ids = [f"advanced:{Path(page['file_name']).stem}:p{page['page']}" for page in pages]
    source_ids = [row["source_id"] for row in _read_jsonl(snapshot / "source_ids.jsonl")]
    page_inputs = _read_jsonl(snapshot / "page_inputs.jsonl")
    if source_ids != page_ids or [row["source_id"] for row in page_inputs] != page_ids:
        raise ValueError("IS 표 스냅샷의 출처 행 순서가 다릅니다.")
    if any(row["input_sha256"] != _sha_bytes(row["text"].encode("utf-8")) for row in page_inputs):
        raise ValueError("IS 표 스냅샷의 입력 텍스트 지문이 다릅니다.")
    vectors = np.load(snapshot / "vectors.npy", mmap_mode="r", allow_pickle=False)
    if vectors.shape != base_vectors.shape or vectors.dtype != np.float32:
        raise ValueError("IS 표 벡터 행 수·차원·자료형이 다릅니다.")
    if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=2e-3):
        raise ValueError("IS 표 벡터가 유한하지 않거나 정규화되지 않았습니다.")
    return np.asarray(vectors)


@dataclass(frozen=True)
class RuleOccurrence:
    rule_id: str
    source_id: str
    case_id: str
    role: str
    line_start: int
    line_end: int
    page: dict[str, Any]
    score: float = 0.0


@dataclass(frozen=True)
class RuleMatch:
    status: str
    rule_ids: tuple[str, ...] = ()
    occurrences: tuple[RuleOccurrence, ...] = ()
    score: float | None = None


class HGRuleOccurrenceIndex:
    def __init__(self, rules: Sequence[Mapping[str, Any]], occurrences: Sequence[RuleOccurrence]):
        self.rules = {row["rule_id"]: dict(row) for row in rules}
        if any(
            rule.get("scope") != "linked_source_context"
            or rule.get("review_status") not in {"source_matched", "source_checked"}
            or rule.get("trust") != "retrieved_evidence"
            for rule in self.rules.values()
        ):
            raise ValueError("검증 범위를 벗어난 HG 규칙을 읽을 수 없습니다.")
        self.occurrences = tuple(occurrences)
        self.by_rule_case: dict[str, dict[str, list[RuleOccurrence]]] = defaultdict(lambda: defaultdict(list))
        for occurrence in self.occurrences:
            self.by_rule_case[occurrence.rule_id][occurrence.case_id].append(occurrence)
        self.rule_pages = [
            {"rule_id": rule_id, "text": self.rules[rule_id]["text"]}
            for rule_id in sorted(self.rules)
        ]
        self.index = BM25Index(self.rule_pages, tokenizer=tokenize_bigram_id) if self.rule_pages else None
        self.role_order = {role: index for index, role in enumerate(ROLES)}
        self.counts = {
            "rules": len(self.rules),
            "occurrences": len(self.occurrences),
            "distinct_cases": len({item.case_id for item in self.occurrences}),
            "occurrences_by_role": dict(Counter(item.role for item in self.occurrences)),
        }

    def match(
        self,
        question: str,
        verified_case_ids: Sequence[str],
        *,
        requested_roles: Sequence[str] = (),
        max_rules: int = 1,
    ) -> RuleMatch:
        if not verified_case_ids:
            return RuleMatch("no_verified_case")
        if self.index is None:
            return RuleMatch("no_hg_rules")
        ranked = self.index.search(question, top_k=len(self.rule_pages))
        if not ranked:
            return RuleMatch("no_rule_match")
        page, score = ranked[0]
        if score <= 0:
            return RuleMatch("no_rule_match")
        query_terms = set(tokenize_bigram_id(question))
        rule_terms = set(tokenize_bigram_id(page["text"]))
        if len(query_terms & rule_terms) < 2:
            return RuleMatch("weak_rule_match", score=score)
        if len(ranked) > 1 and abs(score - ranked[1][1]) < 1e-12:
            if ranked[1][0]["text"] != page["text"]:
                return RuleMatch("ambiguous_rule", score=score)
        best_pages = [page]
        if max_rules > 1:
            for candidate, value in ranked[1:]:
                if len(best_pages) >= max_rules:
                    break
                terms = set(tokenize_bigram_id(candidate["text"]))
                if value > 0 and len(query_terms & terms) >= 2:
                    best_pages.append(candidate)
        selected_ids = tuple(dict.fromkeys(row["rule_id"] for row in best_pages))
        requested = set(requested_roles)
        found = []
        for rule_id in selected_ids:
            for case_id in verified_case_ids:
                found.extend(
                    occurrence
                    for occurrence in self.by_rule_case.get(rule_id, {}).get(case_id, ())
                    if not requested or occurrence.role in requested
                )
        if not found:
            return RuleMatch("no_occurrence_for_verified_case", selected_ids, score=score)
        found.sort(key=lambda item: (item.case_id, self.role_order.get(item.role, 999), item.source_id, item.line_start))
        scored = tuple(RuleOccurrence(**{**item.__dict__, "score": score}) for item in found)
        return RuleMatch("matched", selected_ids, scored, score)


def build_hg_rule_occurrence_snapshot(
    output_root: Path = HG_RULE_SNAPSHOT,
    pages: Sequence[dict[str, Any]] | None = None,
    *,
    curated: Path = CURATED,
    case_index: Path = CASE_INDEX,
) -> Path:
    """Create a separately versioned SQLite snapshot of exact HG rule blocks."""
    if pages is None:
        pages, _ = _read_pages_and_sources()
    pages_by_source = {
        f"advanced:{Path(page['file_name']).stem}:p{page['page']}": page for page in pages
    }
    manifest_path = curated / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    rule_path = curated / "rules.jsonl"
    source_path = curated / "sources.jsonl"
    case_path = case_index / "case_sources.jsonl"
    case_summary_path = case_index / "summary.json"
    for path in (rule_path, source_path):
        name = path.relative_to(curated).as_posix()
        if manifest["outputs"].get(name) != _sha_file(path):
            raise ValueError("정제 규칙 또는 출처 파일의 manifest 지문이 다릅니다.")
    case_summary = json.loads(case_summary_path.read_bytes())
    if case_summary["curation_manifest_sha256"] != _sha_file(manifest_path) or case_summary["case_sources_sha256"] != _sha_file(case_path):
        raise ValueError("HG 사건 출처 인덱스 지문이 정제본과 다릅니다.")
    sources = {row["source_id"]: row for row in read_rows(source_path)}
    if set(sources) - set(pages_by_source):
        raise ValueError("정제 출처 중 복원 원본 페이지가 없는 항목이 있습니다.")
    links_by_source: dict[str, tuple[str, str]] = {}
    for row in read_rows(case_path):
        if row["source_id"] in links_by_source:
            raise ValueError("한 원본 페이지에 둘 이상의 HG 사건 연결이 있습니다.")
        source = sources.get(row["source_id"])
        if (
            source is None
            or _page_family(source["heading"]) != "HG"
            or row["record_role"] not in ROLES
            or row["match_rule"] != "page_heading_case_id"
            or hg_ids(source["heading"]) != {row["case_id"]}
        ):
            raise ValueError("HG 규칙 출처의 사건·역할 연결을 검증할 수 없습니다.")
        links_by_source[row["source_id"]] = row["case_id"], row["record_role"]
    expected_links, review, unmatched = build_index(curated)
    actual_links = list(read_rows(case_path))
    if review or unmatched or expected_links != actual_links:
        raise ValueError("HG 사건·출처 연결을 정제본에서 재현하지 못했습니다.")

    rules_by_id = {row["rule_id"]: row for row in read_rows(rule_path)}
    occurrence_rows = []
    eligible_rules = []
    audit = Counter()
    source_pages = {}
    for rule_id, rule in sorted(rules_by_id.items()):
        if (
            rule.get("scope") != "linked_source_context"
            or rule.get("review_status") not in {"source_matched", "source_checked"}
            or rule.get("trust") != "retrieved_evidence"
        ):
            audit["rejected_rule_scope_or_review"] += 1
            continue
        per_rule = []
        for source_id in rule.get("sources", ()):
            source = sources.get(source_id)
            link = links_by_source.get(source_id)
            if source is None or _page_family(source["heading"]) != "HG" or link is None:
                audit["non_hg_or_unlinked_sources"] += 1
                continue
            page = pages_by_source[source_id]
            source_blocks = blocks(page["text"])
            expected_text = compact(rule["text"])
            spans = [
                block
                for block in source_blocks
                if compact("\n".join(block["lines"])) == expected_text
            ]
            if not spans:
                audit["source_text_mismatch"] += 1
                continue
            case_id, role = link
            for span in spans:
                per_rule.append(
                    {
                        "rule_id": rule_id,
                        "source_id": source_id,
                        "case_id": case_id,
                        "role": role,
                        "line_start": span["start"],
                        "line_end": span["end"],
                        "match_rule": "exact_compact_layout_block",
                    }
                )
                source_pages[source_id] = source
        distinct_cases = {row["case_id"] for row in per_rule}
        if len(distinct_cases) < 2:
            audit["not_repeated_across_cases"] += 1
            continue
        eligible_rules.append({key: rule[key] for key in ("rule_id", "text", "kind", "scope", "review_status", "trust") if key in rule})
        occurrence_rows.extend(per_rule)
    if not eligible_rules or not occurrence_rows:
        raise ValueError("검증된 반복 HG 규칙 출현을 찾지 못했습니다.")

    inputs = {
        "curation_manifest_sha256": _sha_file(manifest_path),
        "rules_sha256": _sha_file(rule_path),
        "sources_sha256": _sha_file(source_path),
        "case_sources_sha256": _sha_file(case_path),
        "page_snapshot_sha256": _page_snapshot_sha(pages),
    }
    output_root = Path(output_root)
    if _published(output_root, HG_RULE_TYPE, inputs):
        return output_root
    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=output_root.parent, prefix=f".{output_root.name}-"))
    connection = None
    try:
        database = staging / "hg_rule_occurrences.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE source_pages (
                source_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                page INTEGER NOT NULL CHECK (page > 0),
                heading TEXT NOT NULL,
                file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
                UNIQUE (book_id, page)
            ) STRICT;
            CREATE TABLE rules (
                rule_id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                kind TEXT NOT NULL,
                scope TEXT NOT NULL CHECK (scope = 'linked_source_context'),
                review_status TEXT NOT NULL CHECK (review_status IN ('source_matched','source_checked')),
                trust TEXT NOT NULL CHECK (trust = 'retrieved_evidence')
            ) STRICT;
            CREATE TABLE rule_occurrences (
                rule_id TEXT NOT NULL REFERENCES rules(rule_id),
                source_id TEXT NOT NULL REFERENCES source_pages(source_id),
                case_id TEXT NOT NULL,
                role TEXT NOT NULL,
                line_start INTEGER NOT NULL CHECK (line_start > 0),
                line_end INTEGER NOT NULL CHECK (line_end >= line_start),
                match_rule TEXT NOT NULL CHECK (match_rule = 'exact_compact_layout_block'),
                PRIMARY KEY (rule_id, source_id, line_start, line_end)
            ) STRICT;
            CREATE INDEX rule_occurrences_case_rule ON rule_occurrences(case_id, rule_id);
            CREATE INDEX rule_occurrences_source ON rule_occurrences(source_id, line_start);
            PRAGMA user_version = 1;
            """
        )
        connection.executemany(
            "INSERT INTO source_pages VALUES (?, ?, ?, ?, ?)",
            [
                (source_id, source["book_id"], source["page"], source["heading"], source["file_sha256"])
                for source_id, source in sorted(source_pages.items())
            ],
        )
        connection.executemany(
            "INSERT INTO rules VALUES (?, ?, ?, ?, ?, ?)",
            [tuple(rule[key] for key in ("rule_id", "text", "kind", "scope", "review_status", "trust")) for rule in eligible_rules],
        )
        connection.executemany(
            "INSERT INTO rule_occurrences VALUES (?, ?, ?, ?, ?, ?, ?)",
            [tuple(row[key] for key in ("rule_id", "source_id", "case_id", "role", "line_start", "line_end", "match_rule")) for row in occurrence_rows],
        )
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or list(connection.execute("PRAGMA foreign_key_check")):
            raise ValueError("HG 규칙 출현 SQLite 무결성 검사가 실패했습니다.")
        counts = {
            "source_pages": connection.execute("SELECT count(*) FROM source_pages").fetchone()[0],
            "rules": connection.execute("SELECT count(*) FROM rules").fetchone()[0],
            "occurrences": connection.execute("SELECT count(*) FROM rule_occurrences").fetchone()[0],
            "rule_kinds": dict(connection.execute("SELECT kind, count(*) FROM rules GROUP BY kind").fetchall()),
            "occurrences_by_role": dict(connection.execute("SELECT role, count(*) FROM rule_occurrences GROUP BY role").fetchall()),
            "audit": dict(audit),
        }
        connection.commit()
        connection.close()
        connection = None
        _publish(
            output_root,
            staging,
            HG_RULE_TYPE,
            inputs,
            {"sqlite_version": sqlite3.sqlite_version, "scope": "linked_source_context", "match_rule": "exact_compact_layout_block"},
            counts,
        )
    finally:
        if connection is not None:
            connection.rollback()
            connection.close()
        if staging.exists():
            shutil.rmtree(staging)
    return output_root


def load_hg_rule_index(snapshot: Path, pages: Sequence[dict[str, Any]]) -> HGRuleOccurrenceIndex:
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot / "manifest.json").read_bytes())
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("snapshot_type") != HG_RULE_TYPE:
        raise ValueError("HG 반복 규칙 스냅샷 버전이 다릅니다.")
    if manifest["inputs"].get("page_snapshot_sha256") != _page_snapshot_sha(pages):
        raise ValueError("HG 반복 규칙 원본 페이지 지문이 다릅니다.")
    if manifest.get("snapshot_id") != _sha_bytes(_canonical_bytes({"inputs": manifest["inputs"], "outputs": manifest["outputs"]})):
        raise ValueError("HG 반복 규칙 manifest 지문이 다릅니다.")
    for name, digest in manifest.get("outputs", {}).items():
        if _sha_file(snapshot / name) != digest:
            raise ValueError("HG 반복 규칙 출력 파일 지문이 다릅니다.")
    database = snapshot / "hg_rule_occurrences.sqlite3"
    uri = f"file:{database.resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("HG 반복 규칙 SQLite 무결성 검사가 실패했습니다.")
        rules = [dict(zip(("rule_id", "text", "kind", "scope", "review_status", "trust"), row, strict=True)) for row in connection.execute("SELECT rule_id,text,kind,scope,review_status,trust FROM rules ORDER BY rule_id")]
        page_by_source = {f"advanced:{Path(page['file_name']).stem}:p{page['page']}": page for page in pages}
        occurrences = []
        query = "SELECT rule_id,source_id,case_id,role,line_start,line_end FROM rule_occurrences ORDER BY rule_id,case_id,role,source_id,line_start"
        for rule_id, source_id, case_id, role, line_start, line_end in connection.execute(query):
            page = page_by_source.get(source_id)
            if page is None or role not in ROLES:
                raise ValueError("HG 반복 규칙 출현의 복원 원본이 없습니다.")
            occurrences.append(RuleOccurrence(rule_id, source_id, case_id, role, line_start, line_end, page))
        return HGRuleOccurrenceIndex(rules, occurrences)
    finally:
        connection.close()


def _evaluation_files(*, include_scenarios: bool, include_categories: bool) -> dict[str, tuple[Path, Path]]:
    files = evaluation_files("categories" if include_categories else "scenarios")
    if not include_scenarios:
        files.pop("scenarios", None)
    standard = evaluation_files()
    files.update({split: standard[split] for split in OFFICIAL_SPLITS})
    return files


def _fact_complete_counts(rows: Sequence[Mapping[str, Any]], arm: str) -> tuple[int, int]:
    scored = [row for row in rows if "final_complete" in row.get("arms", {}).get(arm, {})]
    return sum(row["arms"][arm]["final_complete"] for row in scored), len(scored)


def _write_report(path: Path, title: str, summary: Mapping[str, Any], comparisons: Mapping[str, Any]) -> None:
    lines = [f"# {title}", "", "비교군은 동일 문항·원본 페이지·후보 20쪽·최종 최대 8쪽·8,000토큰 조건을 사용했다.", ""]
    lines.append("## 유형별 전체 근거 충족")
    lines.extend(["", "| 평가 묶음 | 설정 | 전체 근거 충족 | 채점 문항 |", "|---|---|---:|---:|"])
    for category, arms in summary.items():
        if not isinstance(arms, Mapping) or not {"baseline"} <= set(arms):
            continue
        for arm, metrics in arms.items():
            completed, denominator = _fact_complete_counts(
                [row for row in comparisons.get("rows", []) if row["category"] == category], arm
            )
            if denominator:
                lines.append(f"| {category} | {arm} | {completed}/{denominator} | {denominator} |")
    lines.extend(["", "## 문항별 변화", ""])
    lines.append(f"기준선 대비 이득 {comparisons.get('gains', 0)}문항, 하락 {comparisons.get('losses', 0)}문항, 같음 {comparisons.get('unchanged', 0)}문항.")
    lines.extend(["", "실제 질문·참고 정답·원문 인용은 이 보고서에 복사하지 않았다.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def compare_is_tables(args, output: Path, log) -> dict[str, Any]:
    """Compare original, verified-common-block, and labeled-table vectors."""
    started = perf_counter()
    files = _evaluation_files(include_scenarios=True, include_categories=True)
    questions = evaluation_questions(files)
    if len(questions) != 280:
        raise ValueError("IS 렌더링 비교는 공식 60문항과 카테고리 220문항이어야 합니다.")
    pages, baseline_vectors = load_index()
    common_vectors = load_is(args.is_snapshot, pages, baseline_vectors)
    table_vectors = load_is_table_vectors(args.is_table_snapshot, pages, baseline_vectors)
    lookup = load_target_lookup(pages)
    storage = load_storage_lookup(pages)
    search = Search(pages, baseline_vectors)
    arms = ("baseline", "is_common", "is_table")
    vectors = {
        "baseline": baseline_vectors,
        "is_common": common_vectors,
        "is_table": table_vectors,
    }
    retrievals, traces = {}, {}
    log(f"IS 표 재렌더링 비교 시작 · {len(questions)}문항")
    for batch in batches(questions):
        pools, heads = search.rank(batch, vectors, log)
        for question in batch:
            qid, text = question["id"], question["question"]
            for arm in arms:
                pool = pools[arm][next(i for i, row in enumerate(batch) if row["id"] == qid)]
                head = heads[arm][next(i for i, row in enumerate(batch) if row["id"] == qid)]
                order = retain_reranked_head(pool, head)
                result, linked = finish(text, pool, order, lookup, storage)
                retrievals[qid, arm] = result
                traces[qid, arm] = trace(result, linked, lookup, order)
    summary, rows, _ = evaluation_score(output, questions, files, retrievals, traces, arms)
    gains = {
        arm: sum(row.get("final_delta", {}).get(arm) == 1 for row in rows)
        for arm in arms[1:]
    }
    losses = {
        arm: sum(row.get("final_delta", {}).get(arm) == -1 for row in rows)
        for arm in arms[1:]
    }
    unchanged = {
        arm: sum(row.get("final_delta", {}).get(arm) == 0 for row in rows)
        for arm in arms[1:]
    }
    results = {
        "kind": "is_table_rendering",
        "question_count": len(questions),
        "split_counts": dict(Counter(question["split"] for question in questions)),
        "config": {
            "candidate_count": CANDIDATES,
            "context_pages": 8,
            "context_tokens": DEFAULT_CONTEXT_TOKENS,
            "inputs": {split: {"questions": stamp(q), "references": stamp(r)} for split, (q, r) in files.items()},
            "pages": stamp(CACHE_PATH),
            "baseline_vectors": stamp(VECTORS_PATH),
            "common_block_snapshot": stamp(Path(args.is_snapshot) / "manifest.json"),
            "table_snapshot": stamp(Path(args.is_table_snapshot) / "manifest.json"),
            "external_api_calls": 0,
        },
        "summary": summary,
        "gains": gains,
        "losses": losses,
        "unchanged": unchanged,
        "elapsed_seconds": perf_counter() - started,
    }
    write_json(output / "results.json", results)
    _write_report(
        output / "report.md",
        "IS 표 재렌더링 벡터 입력 비교",
        summary,
        {
            "rows": rows,
            "gains": gains.get("is_table", 0),
            "losses": losses.get("is_table", 0),
            "unchanged": unchanged.get("is_table", 0),
        },
    )
    log(f"IS 표 재렌더링 채점 완료 · 이득 {gains['is_table']} · 하락 {losses['is_table']}")
    return results


def compare_hg_rules(args, output: Path, log) -> dict[str, Any]:
    """Compare baseline retrieval with verified HG rule-occurrence enrichment."""
    started = perf_counter()
    files = _evaluation_files(include_scenarios=True, include_categories=False)
    questions = evaluation_questions(files)
    if len(questions) != 200:
        raise ValueError("HG 반복 규칙 비교는 공식 60문항과 HG 상황별 140문항이어야 합니다.")
    pages, vectors = load_index()
    lookup = load_target_lookup(pages)
    storage = load_storage_lookup(pages)
    snapshot = Path(args.hg_rule_snapshot)
    if not snapshot.exists():
        build_hg_rule_occurrence_snapshot(snapshot, pages)
    rule_index = load_hg_rule_index(snapshot, pages)
    search = Search(pages, vectors)
    arms = ("baseline", "hg_rules")
    retrievals, traces = {}, {}
    status_counts = Counter()
    added_pages = 0
    log(f"HG 반복 규칙 비교 시작 · {len(questions)}문항")
    for batch in batches(questions):
        pools, heads = search.rank(batch, {"baseline": vectors}, log)
        for index, question in enumerate(batch):
            qid, text = question["id"], question["question"]
            pool, head = pools["baseline"][index], heads["baseline"][index]
            order = retain_reranked_head(pool, head)
            base, linked = finish(text, pool, order, lookup, storage)
            retrievals[qid, "baseline"] = base
            traces[qid, "baseline"] = trace(base, linked, lookup, order)

            cases = tuple(base[3])
            requested_roles = roles_in(text, getattr(lookup, "rules", None))
            match = rule_index.match(text, cases, requested_roles=requested_roles)
            rule_pages = []
            seen_sources = set()
            for occurrence in match.occurrences:
                if occurrence.source_id not in seen_sources:
                    seen_sources.add(occurrence.source_id)
                    rule_pages.append(occurrence.page)
            status_counts[match.status] += 1
            added_pages += len({location(page) for page in rule_pages} - {location(page) for page, _ in pool})
            candidate_pool = augment(pool, rule_pages)[:CANDIDATES]
            ordered, context, selection = apply_sources(
                text,
                order,
                lookup,
                DEFAULT_CONTEXT_TOKENS,
                rule_pages,
                (order, base[2], base[4]),
            )
            if len(candidate_pool) != CANDIDATES or len({location(page) for page, _ in candidate_pool}) != CANDIDATES:
                raise ValueError("HG 규칙 출처를 넣은 후보가 상위 20쪽 조건을 지키지 않습니다.")
            linked_all = list(linked)
            linked_sources = {location(page) for page in linked_all}
            for page in rule_pages:
                if location(page) not in linked_sources:
                    linked_all.append(page)
                    linked_sources.add(location(page))
            result = (
                candidate_pool,
                ordered,
                context,
                cases,
                f"{base[4]}/rule_occurrence:{match.status}",
            )
            retrievals[qid, "hg_rules"] = result
            variant_trace = trace(result, linked_all, lookup, ordered)
            variant_trace["rule_match"] = {
                "status": match.status,
                "rule_ids": list(match.rule_ids),
                "occurrence_count": len(match.occurrences),
                "source_ids": [item.source_id for item in match.occurrences],
                "case_ids": sorted({item.case_id for item in match.occurrences}),
                "roles": [item.role for item in match.occurrences],
                "line_ranges": [[item.line_start, item.line_end] for item in match.occurrences],
            }
            traces[qid, "hg_rules"] = variant_trace

    summary, rows, _ = evaluation_score(output, questions, files, retrievals, traces, arms)
    gain = sum(row.get("final_delta", {}).get("hg_rules") == 1 for row in rows)
    loss = sum(row.get("final_delta", {}).get("hg_rules") == -1 for row in rows)
    same = sum(row.get("final_delta", {}).get("hg_rules") == 0 for row in rows)
    config = {
        "candidate_count": CANDIDATES,
        "context_pages": 8,
        "context_tokens": DEFAULT_CONTEXT_TOKENS,
        "inputs": {split: {"questions": stamp(q), "references": stamp(r)} for split, (q, r) in files.items()},
        "pages": stamp(CACHE_PATH),
        "rule_snapshot": stamp(snapshot / "manifest.json"),
        "rule_occurrences": rule_index.counts,
        "external_api_calls": 0,
    }
    results = {
        "kind": "hg_repeated_rule_occurrences",
        "question_count": len(questions),
        "split_counts": dict(Counter(question["split"] for question in questions)),
        "config": config,
        "summary": summary,
        "rule_match_statuses": dict(status_counts),
        "added_rule_pages": added_pages,
        "final_gains": gain,
        "final_losses": loss,
        "final_unchanged": same,
        "elapsed_seconds": perf_counter() - started,
    }
    write_json(output / "results.json", results)
    _write_report(
        output / "report.md",
        "HG 반복 규칙 원본 출현 비교",
        summary,
        {"rows": rows, "gains": gain, "losses": loss, "unchanged": same},
    )
    log(f"HG 반복 규칙 채점 완료 · 매치 {sum(status_counts.values()) - status_counts.get('no_verified_case', 0) - status_counts.get('no_rule_match', 0)} · 이득 {gain} · 하락 {loss}")
    return results
