"""Score completed pattern-retrieval runs against frozen source pages."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

ARMS = ("A", "B", "C")
CATEGORIES = ("exact", "variant", "paraphrase", "comparison", "mixed", "ambiguous")
RESOLUTIONS = ("unique", "multiple_targets", "ambiguous", "conflict", "not_applicable")
OBSERVED_RESOLUTIONS = (
    "none",
    "unknown_code",
    "unsupported_exclusion",
    "too_many_targets",
    *RESOLUTIONS,
)
TARGET_RESOLUTIONS = ("unique", "multiple_targets")
ABSTENTION_RESOLUTIONS = (
    "none",
    "unknown_code",
    "unsupported_exclusion",
    "too_many_targets",
    "ambiguous",
    "conflict",
    "not_applicable",
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _fail(message: str) -> ValueError:
    return ValueError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise _fail(f"파일을 읽을 수 없습니다: {path.name}") from exc


def _object_sha(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _read_json(path: Path) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
        return json.loads(raw.decode("utf-8")), raw
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(f"JSON 입력을 읽을 수 없습니다: {path.name}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise _fail(f"JSONL 입력을 읽을 수 없습니다: {path.name}") from exc
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _fail(f"{path.name}:{line_number} JSON을 읽을 수 없습니다.") from exc
        if not isinstance(record, dict):
            raise _fail(f"{path.name}:{line_number} 레코드 형식이 잘못됐습니다.")
        records.append(record)
    return records


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_id_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise _fail(f"{label} 형식이 잘못됐습니다.")
    if len(value) != len(set(value)):
        raise _fail(f"{label}에 중복 ID가 있습니다.")
    return value


def _load_evaluation(
    evaluation_dir: Path,
) -> tuple[
    dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any], str, str
]:
    manifest, manifest_bytes = _read_json(evaluation_dir / "manifest.json")
    questions_value, questions_bytes = _read_json(evaluation_dir / "questions.json")
    references_value, references_bytes = _read_json(evaluation_dir / "references.json")
    if not isinstance(manifest, dict) or not isinstance(questions_value, list):
        raise _fail("평가 입력 manifest 또는 questions 형식이 잘못됐습니다.")
    if not isinstance(references_value, dict):
        raise _fail("평가 references는 ID별 객체여야 합니다.")

    question_hash = _sha256_bytes(questions_bytes)
    references_hash = _sha256_bytes(references_bytes)
    if manifest.get("questions_sha256") != question_hash:
        raise _fail("평가 manifest의 questions_sha256가 questions.json과 다릅니다.")
    if manifest.get("references_sha256") != references_hash:
        raise _fail("평가 manifest의 references_sha256가 references.json과 다릅니다.")

    questions: dict[str, dict[str, Any]] = {}
    for item in questions_value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise _fail("questions.json에 유효하지 않은 문항이 있습니다.")
        if not isinstance(item.get("question"), str):
            raise _fail("questions.json에 질문 문자열이 없습니다.")
        question_id = item["id"]
        if question_id in questions:
            raise _fail("questions.json에 중복 ID가 있습니다.")
        questions[question_id] = item
    if not questions:
        raise _fail("questions.json이 비어 있습니다.")
    if set(references_value) != set(questions):
        raise _fail("questions.json과 references.json의 ID 집합이 다릅니다.")

    references: dict[str, dict[str, Any]] = {}
    for question_id, reference in references_value.items():
        if not isinstance(reference, dict):
            raise _fail("references.json에 유효하지 않은 항목이 있습니다.")
        split = reference.get("split")
        category = reference.get("category")
        resolution = reference.get("resolution_expected")
        if split not in ("development", "validation"):
            raise _fail("references.json의 split 값이 잘못됐습니다.")
        if category not in CATEGORIES:
            raise _fail("references.json의 category 값이 잘못됐습니다.")
        if resolution not in RESOLUTIONS:
            raise _fail("references.json의 resolution_expected 값이 잘못됐습니다.")
        expected_cases = reference.get("expected_cases")
        if not isinstance(expected_cases, list) or any(
            not isinstance(case, str) for case in expected_cases
        ):
            raise _fail("references.json의 expected_cases 형식이 잘못됐습니다.")
        if len(expected_cases) != len(set(expected_cases)):
            raise _fail("references.json의 expected_cases에 중복 값이 있습니다.")
        requirements = reference.get("requirements")
        if not isinstance(requirements, list):
            raise _fail("references.json의 requirements 형식이 잘못됐습니다.")
        references[question_id] = {
            "split": split,
            "category": category,
            "resolution_expected": resolution,
            "expected_cases": set(expected_cases),
            "requirements_raw": requirements,
        }

    _validate_evaluation_manifest(manifest, references)
    return (
        questions,
        references,
        manifest,
        question_hash,
        _sha256_bytes(manifest_bytes),
    )


def _validate_evaluation_manifest(
    manifest: dict[str, Any], references: dict[str, dict[str, Any]]
) -> None:
    ids = set(references)
    split_ids = manifest.get("split_ids")
    if not isinstance(split_ids, dict):
        raise _fail("평가 manifest의 split_ids 형식이 잘못됐습니다.")
    for split in ("development", "validation"):
        listed = set(_validate_id_list(split_ids.get(split), f"split_ids.{split}"))
        actual = {key for key, value in references.items() if value["split"] == split}
        if listed != actual:
            raise _fail(f"평가 manifest의 {split} ID가 references와 다릅니다.")
    if set().union(*(set(v) for v in split_ids.values() if isinstance(v, list))) != ids:
        raise _fail("평가 manifest의 split_ids가 문항 ID 전체를 덮지 않습니다.")

    categories = manifest.get("categories")
    if not isinstance(categories, dict):
        raise _fail("평가 manifest의 categories 형식이 잘못됐습니다.")
    for category in CATEGORIES:
        listed = set(
            _validate_id_list(categories.get(category), f"categories.{category}")
        )
        actual = {
            key for key, value in references.items() if value["category"] == category
        }
        if listed != actual:
            raise _fail(f"평가 manifest의 {category} ID가 references와 다릅니다.")
    if (
        set().union(*(set(v) for v in categories.values() if isinstance(v, list)))
        != ids
    ):
        raise _fail("평가 manifest의 categories가 문항 ID 전체를 덮지 않습니다.")

    listed_timing_ids = _validate_id_list(manifest.get("timing_ids"), "timing_ids")
    if len(listed_timing_ids) != 30 or not set(listed_timing_ids) <= ids:
        raise _fail("평가 manifest의 timing_ids는 고정된 30개 문항이어야 합니다.")
    if any(references[item]["split"] != "validation" for item in listed_timing_ids):
        raise _fail("timing_ids에는 validation 문항만 포함할 수 있습니다.")
    for category in CATEGORIES:
        category_ids = set(categories[category])
        if len(category_ids & set(listed_timing_ids)) != 5:
            raise _fail("timing_ids에는 각 category에서 5개 문항이 있어야 합니다.")


def _runtime_fingerprint(runtime_files: Any, expected_code_hash: Any) -> str:
    if not isinstance(runtime_files, list) or not runtime_files:
        raise _fail("retrieval manifest의 runtime_files가 비어 있거나 잘못됐습니다.")
    normalized: list[dict[str, str]] = []
    root = PROJECT_ROOT.resolve()
    for item in runtime_files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise _fail("runtime_files 항목은 path와 sha256만 가져야 합니다.")
        relative = item.get("path")
        file_hash = item.get("sha256")
        if not isinstance(relative, str) or not isinstance(file_hash, str):
            raise _fail("runtime_files 경로 또는 sha256 형식이 잘못됐습니다.")
        if len(file_hash) != 64 or any(c not in "0123456789abcdef" for c in file_hash):
            raise _fail("runtime_files에 잘못된 sha256 값이 있습니다.")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise _fail("runtime_files 경로는 프로젝트 상대 경로여야 합니다.")
        absolute = (root / relative_path).resolve()
        try:
            absolute.relative_to(root)
        except ValueError as exc:
            raise _fail("runtime_files 경로가 프로젝트 범위를 벗어났습니다.") from exc
        try:
            actual_hash = _sha256_bytes(absolute.read_bytes())
        except OSError as exc:
            raise _fail("runtime_files의 현행 파일을 읽을 수 없습니다.") from exc
        if actual_hash != file_hash:
            raise _fail("runtime_files의 현행 파일 해시가 manifest와 다릅니다.")
        normalized.append({"path": relative, "sha256": file_hash})

    normalized.sort(key=lambda row: row["path"])
    fingerprint = _sha256_bytes(
        json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    if expected_code_hash != fingerprint:
        raise _fail("retrieval manifest의 code_sha256가 runtime_files와 다릅니다.")
    return fingerprint


def _load_corpus(
    corpus_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, int], str], set[tuple[str, str]]]:
    try:
        connection = sqlite3.connect(f"file:{corpus_path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT source_id, book_id, page, text FROM pages"
        ).fetchall()
        occurrence_rows = connection.execute(
            "SELECT unit_id, source_id FROM occurrences"
        ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        raise _fail("frozen corpus의 pages/occurrences를 읽을 수 없습니다.") from exc
    finally:
        if "connection" in locals():
            connection.close()

    pages: dict[str, dict[str, Any]] = {}
    by_book_page: dict[tuple[str, int], str] = {}
    for row in rows:
        source_id = row["source_id"]
        book_id = row["book_id"]
        page = row["page"]
        text = row["text"]
        if (
            not isinstance(source_id, str)
            or not isinstance(book_id, str)
            or not isinstance(page, int)
            or not isinstance(text, str)
            or source_id in pages
            or (book_id, page) in by_book_page
        ):
            raise _fail("frozen corpus pages에 중복 또는 잘못된 출처가 있습니다.")
        pages[source_id] = {"book_id": book_id, "page": page, "text": text}
        by_book_page[(book_id, page)] = source_id
    occurrences = {
        (str(row["unit_id"]), str(row["source_id"])) for row in occurrence_rows
    }
    if not pages:
        raise _fail("frozen corpus pages가 비어 있습니다.")
    return pages, by_book_page, occurrences


def _load_index_statistics(
    index_dir: Path,
    vectors_manifest: dict[str, Any],
    page_count: int,
) -> dict[str, Any]:
    corpus_manifest, _ = _read_json(index_dir / "corpus-manifest.json")
    if (
        not isinstance(corpus_manifest, dict)
        or corpus_manifest.get("database") != "corpus.sqlite3"
    ):
        raise _fail("corpus manifest 형식 또는 database가 잘못됐습니다.")
    counts = corpus_manifest.get("counts")
    if not isinstance(counts, dict) or counts.get("pages") != page_count:
        raise _fail("corpus manifest의 page count가 frozen database와 다릅니다.")
    integrity = corpus_manifest.get("integrity")
    if not isinstance(integrity, dict) or any(
        integrity.get(key) != expected
        for key, expected in (
            ("foreign_keys", "ok"),
            ("page_units", True),
            ("body_block_coverage", True),
            ("code_links", True),
        )
    ):
        raise _fail("corpus manifest의 integrity 확인이 통과하지 않았습니다.")
    units: dict[str, int] = {"A": 0, "B": 0}
    try:
        with sqlite3.connect(
            f"file:{(index_dir / 'corpus.sqlite3').as_posix()}?mode=ro", uri=True
        ) as connection:
            for arm, count in connection.execute(
                "SELECT arm, count(*) FROM units GROUP BY arm"
            ):
                if arm not in units:
                    raise _fail("corpus units에 A/B 이외 arm이 있습니다.")
                units[arm] = count
    except sqlite3.Error as exc:
        raise _fail("corpus units별 개수를 읽을 수 없습니다.") from exc
    if sum(units.values()) != counts.get("units"):
        raise _fail("corpus manifest의 unit count가 database와 다릅니다.")

    statistics = vectors_manifest.get("statistics")
    if not isinstance(statistics, dict):
        raise _fail("vectors manifest의 statistics 형식이 잘못됐습니다.")
    numeric_fields = (
        "embedding_seconds",
        "raw_tokens",
        "input_tokens",
        "truncated_units",
        "encoded_units",
        "reused_identical_texts",
    )
    selected_stats: dict[str, int | float] = {}
    for key in numeric_fields:
        value = statistics.get(key)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or value < 0
        ):
            raise _fail("vectors manifest의 embedding statistics가 잘못됐습니다.")
        selected_stats[key] = value
    source_count = corpus_manifest.get("source_count")
    dimension = vectors_manifest.get("dimension")
    if (
        not isinstance(source_count, int)
        or isinstance(source_count, bool)
        or not isinstance(dimension, int)
        or isinstance(dimension, bool)
        or dimension <= 0
    ):
        raise _fail("corpus source_count 또는 vector dimension이 잘못됐습니다.")
    return {
        "source_count": source_count,
        "page_count": page_count,
        "unit_counts": units,
        "occurrence_count": counts.get("occurrences"),
        "code_link_count": counts.get("code_links"),
        "database_bytes": (index_dir / "corpus.sqlite3").stat().st_size,
        "vectors_bytes": (index_dir / "vectors.npy").stat().st_size,
        "vector_dimension": dimension,
        **selected_stats,
    }


def _compact_whitespace(value: str) -> str:
    return " ".join(value.split())


def _compile_references(
    references: dict[str, dict[str, Any]],
    by_book_page: dict[tuple[str, int], str],
    pages: dict[str, dict[str, Any]],
) -> None:
    for reference in references.values():
        compiled_requirements: list[list[set[str]]] = []
        primary_sources: set[str] = set()
        raw_requirements = reference.pop("requirements_raw")
        for requirement in raw_requirements:
            if not isinstance(requirement, dict):
                raise _fail("reference requirement 형식이 잘못됐습니다.")
            primary = requirement.get("primary")
            alternatives = requirement.get("alternatives", [])
            if not isinstance(primary, list) or not primary:
                raise _fail("reference requirement의 primary citation이 비어 있습니다.")
            if not isinstance(alternatives, list):
                raise _fail("reference requirement의 alternatives 형식이 잘못됐습니다.")

            options: list[set[str]] = []
            primary_group = _compile_citation_group(primary, by_book_page, pages)
            options.append(primary_group)
            primary_sources.update(primary_group)
            for alternative in alternatives:
                if not isinstance(alternative, list) or not alternative:
                    raise _fail(
                        "reference alternative는 비어 있지 않은 citation 목록이어야 합니다."
                    )
                options.append(
                    _compile_citation_group(alternative, by_book_page, pages)
                )
            compiled_requirements.append(options)
        reference["requirements"] = compiled_requirements
        reference["primary_sources"] = primary_sources


def _compile_citation_group(
    citations: list[Any],
    by_book_page: dict[tuple[str, int], str],
    pages: dict[str, dict[str, Any]],
) -> set[str]:
    sources: set[str] = set()
    for citation in citations:
        if not isinstance(citation, dict):
            raise _fail("reference citation 형식이 잘못됐습니다.")
        book_id, page, quote = (
            citation.get("book_id"),
            citation.get("page"),
            citation.get("quote"),
        )
        if (
            not isinstance(book_id, str)
            or not isinstance(page, int)
            or isinstance(page, bool)
            or not isinstance(quote, str)
            or not quote.strip()
        ):
            raise _fail("reference citation의 book_id/page/quote가 잘못됐습니다.")
        source_id = by_book_page.get((book_id, page))
        if source_id is None:
            raise _fail(
                "reference citation이 frozen corpus의 같은 book/page에 없습니다."
            )
        if _compact_whitespace(quote) not in _compact_whitespace(
            pages[source_id]["text"]
        ):
            raise _fail(
                "reference quote가 frozen corpus의 같은 source/page와 일치하지 않습니다."
            )
        sources.add(source_id)
    if not sources:
        raise _fail("reference citation group에 출처가 없습니다.")
    return sources


def _trace_sources(
    value: Any,
    label: str,
    pages: dict[str, dict[str, Any]],
    occurrences: set[tuple[str, str]],
    *,
    unit_candidates: bool,
    maximum: int,
) -> tuple[list[str] | None, set[tuple[str, str]] | None]:
    if value is None:
        return None, None
    if not isinstance(value, list) or len(value) > maximum:
        raise _fail(f"trace의 {label} 형식 또는 최대 개수가 잘못됐습니다.")
    source_order: list[str] = []
    candidate_pairs: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise _fail(f"trace의 {label} 항목 형식이 잘못됐습니다.")
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or source_id not in pages:
            raise _fail(f"trace의 {label}에 corpus 밖 source_id가 있습니다.")
        if (
            item.get("book_id") != pages[source_id]["book_id"]
            or item.get("page") != pages[source_id]["page"]
        ):
            raise _fail(f"trace의 {label} source_id와 book/page가 다릅니다.")
        if unit_candidates:
            unit_id = item.get("unit_id")
            if not isinstance(unit_id, str) or (unit_id, source_id) not in occurrences:
                raise _fail(
                    f"trace의 {label} unit/source 조합이 corpus occurrences에 없습니다."
                )
            pair = (unit_id, source_id)
            if pair in candidate_pairs:
                raise _fail(f"trace의 {label}에 중복 candidate가 있습니다.")
            candidate_pairs.add(pair)
        if source_id not in source_order:
            source_order.append(source_id)
    return source_order, candidate_pairs if unit_candidates else None


def _validate_final_pages(
    value: Any, pages: dict[str, dict[str, Any]]
) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 8:
        raise _fail("trace의 final_pages 형식 또는 최대 개수가 잘못됐습니다.")
    sources: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise _fail("trace의 final_pages 항목 형식이 잘못됐습니다.")
        source_id = item.get("source_id")
        if not isinstance(source_id, str) or source_id not in pages:
            raise _fail("trace final_pages에 corpus 밖 source_id가 있습니다.")
        if (
            item.get("book_id") != pages[source_id]["book_id"]
            or item.get("page") != pages[source_id]["page"]
        ):
            raise _fail("trace final_pages source_id와 book/page가 다릅니다.")
        if source_id in sources:
            raise _fail("trace final_pages에 중복 출처가 있습니다.")
        sources.append(source_id)
    return sources


def _validate_context(
    context: dict[str, Any] | None,
    final_sources: list[str] | None,
    pages: dict[str, dict[str, Any]],
    by_book_page: dict[tuple[str, int], str],
) -> bool:
    if context is None:
        return False
    context_pages = context.get("pages")
    if not isinstance(context_pages, list):
        raise _fail("contexts.jsonl의 pages 형식이 잘못됐습니다.")
    actual_sources: list[str] = []
    for item in context_pages:
        if not isinstance(item, dict):
            raise _fail("contexts.jsonl의 page 항목 형식이 잘못됐습니다.")
        book_id, page, text = item.get("book_id"), item.get("page"), item.get("text")
        if (
            not isinstance(book_id, str)
            or not isinstance(page, int)
            or not isinstance(text, str)
        ):
            raise _fail("contexts.jsonl의 page 출처 형식이 잘못됐습니다.")
        source_id = by_book_page.get((book_id, page))
        if source_id is None or text != pages[source_id]["text"]:
            raise _fail("contexts.jsonl의 원문이 frozen corpus와 다릅니다.")
        actual_sources.append(source_id)
    if final_sources is None:
        if actual_sources:
            raise _fail("final_pages가 없는 trace에 context 원문이 있습니다.")
    elif actual_sources != final_sources:
        raise _fail("contexts.jsonl 출처 순서가 final_pages와 다릅니다.")
    return True


def _validate_relation(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _fail("trace의 relation 형식이 잘못됐습니다.")
    status = value.get("status")
    cases = value.get("cases")
    if (
        status not in OBSERVED_RESOLUTIONS
        or not isinstance(cases, list)
        or any(not isinstance(case, str) for case in cases)
    ):
        raise _fail("trace의 relation status/cases 형식이 잘못됐습니다.")
    if len(cases) != len(set(cases)):
        raise _fail("trace의 relation cases에 중복 값이 있습니다.")
    return {"status": status, "cases": set(cases)}


def _question_score(
    reference: dict[str, Any],
    arm: str,
    trace: dict[str, Any],
    context: dict[str, Any] | None,
    pages: dict[str, dict[str, Any]],
    by_book_page: dict[tuple[str, int], str],
    occurrences: set[tuple[str, str]],
) -> dict[str, Any]:
    missing: list[str] = []
    pool, pool_pairs = _trace_sources(
        trace.get("pool"), "pool", pages, occurrences, unit_candidates=True, maximum=20
    )
    ranked, ranked_pairs = _trace_sources(
        trace.get("ranked"),
        "ranked",
        pages,
        occurrences,
        unit_candidates=True,
        maximum=20,
    )
    final = _validate_final_pages(trace.get("final_pages"), pages)
    if "pool" not in trace or pool is None:
        missing.append("pool")
    if "ranked" not in trace or ranked is None:
        missing.append("ranked")
    if "final_pages" not in trace or final is None:
        missing.append("final_pages")
    if (
        pool_pairs is not None
        and ranked_pairs is not None
        and pool_pairs != ranked_pairs
    ):
        raise _fail("trace의 ranked와 pool candidate 집합이 다릅니다.")
    if ranked_pairs is not None and pool_pairs is None:
        raise _fail("pool 없이 ranked candidates가 있습니다.")

    context_present = _validate_context(context, final, pages, by_book_page)
    if not context_present:
        missing.append("context")
    if arm == "C":
        relation = _validate_relation(trace.get("relation"))
        if relation is None:
            missing.append("relation")
    else:
        relation = None

    primary = reference["primary_sources"]
    primary_count = len(primary)
    pool_set = set(pool or [])
    final_set = set(final or [])
    recall20 = (
        len(primary & pool_set) / primary_count
        if pool is not None and primary_count
        else None
    )
    recall8 = (
        len(primary & final_set) / primary_count
        if final is not None and primary_count
        else None
    )
    first_relevant = next(
        (
            index
            for index, source_id in enumerate((final or [])[:8], 1)
            if source_id in primary
        ),
        None,
    )
    mrr8 = (
        (1 / first_relevant)
        if first_relevant is not None
        else (0.0 if final is not None and primary_count else None)
    )
    dcg = sum(
        1 / math.log2(index + 1)
        for index, source_id in enumerate((final or [])[:8], 1)
        if source_id in primary
    )
    ideal_count = min(primary_count, 8)
    idcg = sum(1 / math.log2(index + 1) for index in range(1, ideal_count + 1))
    ndcg8 = (dcg / idcg) if final is not None and idcg else None

    def supported_count(sources: set[str]) -> int:
        return sum(
            any(option <= sources for option in options)
            for options in reference["requirements"]
        )

    candidate_support = (
        [
            any(option <= pool_set for option in options)
            for options in reference["requirements"]
        ]
        if pool is not None
        else None
    )
    final_support = (
        [
            any(option <= final_set for option in options)
            for options in reference["requirements"]
        ]
        if final is not None
        else None
    )
    candidate_facts = sum(candidate_support) if candidate_support is not None else None
    final_facts = sum(final_support) if final_support is not None else None
    requirement_count = len(reference["requirements"])
    if final is not None and ranked is not None and not final_set <= set(ranked):
        raise _fail("trace final_pages가 ranked pool에 없습니다.")
    candidate_missing_facts = (
        requirement_count - candidate_facts if candidate_facts is not None else None
    )
    dropped_after_candidate = (
        sum(
            candidate and not final_hit
            for candidate, final_hit in zip(candidate_support, final_support)
        )
        if candidate_support is not None and final_support is not None
        else None
    )
    first_missing_stage = None
    if "pool" in missing:
        first_missing_stage = "pool"
    elif requirement_count and candidate_missing_facts:
        first_missing_stage = "candidate_evidence"
    elif "ranked" in missing:
        first_missing_stage = "ranked"
    elif "final_pages" in missing:
        first_missing_stage = "final_pages"
    elif dropped_after_candidate:
        first_missing_stage = "final_selection"
    elif "context" in missing:
        first_missing_stage = "context"
    elif "relation" in missing:
        first_missing_stage = "relation"

    result: dict[str, Any] = {
        "primary_page_count": primary_count,
        "recall20": recall20,
        "recall8": recall8,
        "mrr8": mrr8,
        "ndcg8": ndcg8,
        "candidate_facts": candidate_facts,
        "candidate_missing_facts": candidate_missing_facts,
        "candidate_requirements": requirement_count,
        "candidate_complete": (
            candidate_facts == requirement_count
            if candidate_facts is not None and requirement_count
            else None
        ),
        "final_facts": final_facts,
        "dropped_after_candidate_facts": dropped_after_candidate,
        "final_requirements": requirement_count,
        "final_complete": (
            final_facts == requirement_count
            if final_facts is not None and requirement_count
            else None
        ),
        "missing_stages": missing,
        "first_missing_stage": first_missing_stage,
    }
    if arm == "C":
        expected_resolution = reference["resolution_expected"]
        expected_cases = reference["expected_cases"]
        observed_status = relation["status"] if relation else None
        observed_cases = relation["cases"] if relation else set()
        expected_has_targets = expected_resolution in TARGET_RESOLUTIONS
        target_correct = (
            observed_status == expected_resolution and observed_cases == expected_cases
            if expected_has_targets and observed_status is not None
            else None
        )
        asserted_target = observed_status in TARGET_RESOLUTIONS
        exact_status_match = (
            observed_status == expected_resolution
            if observed_status is not None
            else None
        )
        abstention_interpretation_correct = (
            expected_resolution in ABSTENTION_RESOLUTIONS
            and observed_status in ABSTENTION_RESOLUTIONS
            if observed_status is not None
            else None
        )
        resolution_status_correct = (
            bool(exact_status_match or abstention_interpretation_correct)
            if observed_status is not None
            else None
        )
        false_confirmation = (
            bool(
                observed_status == "unique"
                and not (
                    expected_resolution == "unique" and observed_cases == expected_cases
                )
            )
            if observed_status is not None
            else None
        )
        false_positive = (
            bool(asserted_target and not target_correct)
            if observed_status is not None
            else None
        )
        abstain = (
            bool(expected_has_targets and observed_status in ABSTENTION_RESOLUTIONS)
            if observed_status is not None
            else None
        )
        result.update(
            {
                "expected_resolution": expected_resolution,
                "observed_resolution": observed_status,
                "resolution_status_correct": resolution_status_correct,
                "resolution_exact_match": exact_status_match,
                "abstention_interpretation_correct": abstention_interpretation_correct,
                "target_correct": target_correct,
                "false_confirmation": false_confirmation,
                "false_positive": false_positive,
                "abstain": abstain,
            }
        )
    else:
        result.update(
            {
                "expected_resolution": "not_applicable",
                "observed_resolution": "not_applicable",
                "resolution_status_correct": None,
                "resolution_exact_match": None,
                "abstention_interpretation_correct": None,
                "target_correct": None,
                "false_confirmation": None,
                "false_positive": None,
                "abstain": None,
            }
        )
    return result


def _mean(values: list[float | int | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _aggregate_question_results(
    question_results: list[dict[str, Any]],
    *,
    include_ambiguous: bool,
) -> dict[str, Any]:
    selected = [
        item
        for item in question_results
        if include_ambiguous or item["category"] != "ambiguous"
    ]
    per_arm: dict[str, Any] = {}
    for arm in ARMS:
        records = [item["arms"][arm] for item in selected]
        fact_denominator = sum(record["candidate_requirements"] for record in records)
        final_denominator = sum(record["final_requirements"] for record in records)
        candidate_fact_hits = sum(record["candidate_facts"] or 0 for record in records)
        final_fact_hits = sum(record["final_facts"] or 0 for record in records)
        candidate_complete = [record["candidate_complete"] for record in records]
        final_complete = [record["final_complete"] for record in records]
        candidate_complete_present = [
            value for value in candidate_complete if value is not None
        ]
        final_complete_present = [
            value for value in final_complete if value is not None
        ]
        relation_records = (
            [record for record in records if record["observed_resolution"] is not None]
            if arm == "C"
            else []
        )
        target_records = [
            record
            for record in relation_records
            if record["target_correct"] is not None
        ]
        status_records = [
            record
            for record in relation_records
            if record["resolution_status_correct"] is not None
        ]
        exact_status_records = [
            record
            for record in relation_records
            if record["resolution_exact_match"] is not None
        ]
        abstention_interpretation_records = [
            record
            for record in relation_records
            if record["abstention_interpretation_correct"] is not None
            and record["expected_resolution"] in ABSTENTION_RESOLUTIONS
        ]
        confirmation_records = [
            record
            for record in relation_records
            if record["false_confirmation"] is not None
        ]
        false_positive_records = [
            record
            for record in relation_records
            if record["false_positive"] is not None
        ]
        abstain_records = [
            record
            for record in relation_records
            if record["expected_resolution"] in ("unique", "multiple_targets")
            and record["abstain"] is not None
        ]
        ambiguous_records = [
            record
            for record in relation_records
            if record["expected_resolution"] == "ambiguous"
            and record["false_confirmation"] is not None
        ]
        per_arm[arm] = {
            "n": len(records),
            "recall20": _mean([record["recall20"] for record in records]),
            "recall8": _mean([record["recall8"] for record in records]),
            "mrr8": _mean([record["mrr8"] for record in records]),
            "ndcg8": _mean([record["ndcg8"] for record in records]),
            "candidate_fact_coverage": _rate(candidate_fact_hits, fact_denominator),
            "candidate_complete_rate": _rate(
                sum(value is True for value in candidate_complete_present),
                len(candidate_complete_present),
            ),
            "candidate_complete_n": len(candidate_complete_present),
            "final_fact_coverage": _rate(final_fact_hits, final_denominator),
            "final_complete_rate": _rate(
                sum(value is True for value in final_complete_present),
                len(final_complete_present),
            ),
            "final_complete_n": len(final_complete_present),
            "target_accuracy": _rate(
                sum(record["target_correct"] is True for record in target_records),
                len(target_records),
            )
            if arm == "C"
            else None,
            "target_accuracy_n": len(target_records) if arm == "C" else 0,
            "resolution_status_accuracy": _rate(
                sum(
                    record["resolution_status_correct"] is True
                    for record in status_records
                ),
                len(status_records),
            )
            if arm == "C"
            else None,
            "resolution_status_accuracy_n": len(status_records) if arm == "C" else 0,
            "resolution_exact_match_rate": _rate(
                sum(
                    record["resolution_exact_match"] is True
                    for record in exact_status_records
                ),
                len(exact_status_records),
            )
            if arm == "C"
            else None,
            "resolution_exact_match_n": len(exact_status_records) if arm == "C" else 0,
            "abstention_interpretation_rate": _rate(
                sum(
                    record["abstention_interpretation_correct"] is True
                    for record in abstention_interpretation_records
                ),
                len(abstention_interpretation_records),
            )
            if arm == "C"
            else None,
            "abstention_interpretation_n": (
                len(abstention_interpretation_records) if arm == "C" else 0
            ),
            "false_confirmation_rate": _rate(
                sum(
                    record["false_confirmation"] is True
                    for record in confirmation_records
                ),
                len(confirmation_records),
            )
            if arm == "C"
            else None,
            "false_confirmation_n": len(confirmation_records) if arm == "C" else 0,
            "false_positive_rate": _rate(
                sum(
                    record["false_positive"] is True
                    for record in false_positive_records
                ),
                len(false_positive_records),
            )
            if arm == "C"
            else None,
            "false_positive_n": len(false_positive_records) if arm == "C" else 0,
            "abstain_rate": _rate(
                sum(record["abstain"] is True for record in abstain_records),
                len(abstain_records),
            )
            if arm == "C"
            else None,
            "abstain_n": len(abstain_records) if arm == "C" else 0,
            "ambiguous_false_confirmation_rate": _rate(
                sum(
                    record["false_confirmation"] is True for record in ambiguous_records
                ),
                len(ambiguous_records),
            )
            if arm == "C"
            else None,
            "ambiguous_false_confirmation_n": len(ambiguous_records)
            if arm == "C"
            else 0,
            "ambiguous_false_positive_rate": _rate(
                sum(record["false_positive"] is True for record in ambiguous_records),
                len(ambiguous_records),
            )
            if arm == "C"
            else None,
            "ambiguous_false_positive_n": len(ambiguous_records) if arm == "C" else 0,
        }
    return {"n": len(selected), "arms": per_arm}


def _summarize(question_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_split: dict[str, Any] = {}
    for split in ("development", "validation"):
        group = [item for item in question_results if item["split"] == split]
        by_split[split] = _aggregate_question_results(group, include_ambiguous=False)
    by_category = {
        category: _aggregate_question_results(
            [item for item in question_results if item["category"] == category],
            include_ambiguous=True,
        )
        for category in CATEGORIES
    }
    return {
        "performance_excludes_category": "ambiguous",
        "performance": _aggregate_question_results(
            question_results, include_ambiguous=False
        ),
        "ambiguous": _aggregate_question_results(
            [item for item in question_results if item["category"] == "ambiguous"],
            include_ambiguous=True,
        ),
        "by_split": by_split,
        "by_category": by_category,
        "by_split_category": {
            split: {
                category: _aggregate_question_results(
                    [
                        item
                        for item in question_results
                        if item["split"] == split and item["category"] == category
                    ],
                    include_ambiguous=True,
                )
                for category in CATEGORIES
            }
            for split in ("development", "validation")
        },
    }


def score_run(
    corpus_dir: str | Path,
    retrieval_dir: str | Path,
    evaluation_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Score one completed run; returns and writes aggregate, source-free results."""
    corpus_dir = Path(corpus_dir)
    retrieval_dir = Path(retrieval_dir)
    evaluation_dir = Path(evaluation_dir)
    output_dir = Path(output_dir)

    corpus_path = corpus_dir / "corpus.sqlite3"
    corpus_hash = _sha256_file(corpus_path)
    vectors_manifest_path = corpus_dir / "vectors-manifest.json"
    vectors_manifest, vectors_manifest_bytes = _read_json(vectors_manifest_path)
    if (
        not isinstance(vectors_manifest, dict)
        or vectors_manifest.get("status") != "complete"
    ):
        raise _fail("vectors manifest가 completed 상태가 아닙니다.")
    identity = vectors_manifest.get("identity")
    if not isinstance(identity, dict) or identity.get("corpus_sha256") != corpus_hash:
        raise _fail("vectors manifest의 corpus hash가 frozen corpus와 다릅니다.")
    vectors_hash = _sha256_file(corpus_dir / "vectors.npy")
    if vectors_manifest.get("vectors_sha256") != vectors_hash:
        raise _fail("vectors manifest의 vectors_sha256가 vectors.npy와 다릅니다.")
    retrieval_manifest, retrieval_manifest_bytes = _read_json(
        retrieval_dir / "manifest.json"
    )
    if (
        not isinstance(retrieval_manifest, dict)
        or retrieval_manifest.get("status") != "complete"
    ):
        raise _fail("retrieval manifest가 completed 상태가 아닙니다.")
    # Verify saved results before opening reference answers.
    traces = _read_jsonl(retrieval_dir / "traces.jsonl")
    contexts = _read_jsonl(retrieval_dir / "contexts.jsonl")
    for filename, field in (
        ("traces.jsonl", "traces_sha256"),
        ("contexts.jsonl", "contexts_sha256"),
    ):
        if retrieval_manifest.get(field) != _sha256_file(retrieval_dir / filename):
            raise _fail("검색 결과 파일 지문이 일치하지 않습니다.")
    saved_ids = _validate_id_list(
        retrieval_manifest.get("question_ids"), "question_ids"
    )

    question_preview, preview_bytes = _read_json(evaluation_dir / "questions.json")
    if not isinstance(question_preview, list) or any(
        not isinstance(row, dict) or set(row) != {"id", "question"}
        for row in question_preview
    ):
        raise _fail("질문 입력은 ID와 질문만 포함해야 합니다.")
    expected_question_ids = _validate_id_list(
        [row["id"] for row in question_preview], "questions IDs"
    )
    if set(saved_ids) != set(expected_question_ids):
        raise _fail("참고 근거를 읽기 전에 모든 입력 질문의 검색 결과가 필요합니다.")
    if retrieval_manifest.get("questions_sha256") != _sha256_bytes(preview_bytes):
        raise _fail("검색 입력 질문의 지문이 다릅니다.")
    frozen_dataset, _ = _read_json(evaluation_dir / "manifest.json")
    split_ids = frozen_dataset.get("split_ids", {})
    frozen_ids = [
        qid
        for split in ("development", "validation")
        for qid in _validate_id_list(split_ids.get(split), f"split_ids.{split}")
    ]
    if len(frozen_ids) != len(set(frozen_ids)) or set(frozen_ids) != set(
        expected_question_ids
    ):
        raise _fail("사전에 고정한 평가 세트의 모든 질문을 검색해야 합니다.")
    if frozen_dataset.get("questions_sha256") != _sha256_bytes(preview_bytes):
        raise _fail("고정 평가 manifest와 질문 파일 지문이 다릅니다.")
    if set(_validate_id_list(retrieval_manifest.get("arms"), "arms")) != set(ARMS):
        raise _fail("A/B/C 결과가 모두 필요합니다.")
    if retrieval_manifest.get("trace_count") != len(saved_ids) * len(ARMS):
        raise _fail("검색 결과 개수가 manifest와 다릅니다.")
    expected_saved = {(qid, arm) for qid in saved_ids for arm in ARMS}
    if len(traces) != len(expected_saved) or len(contexts) != len(expected_saved):
        raise _fail(
            "모든 질문의 검색 결과와 원문 문맥 저장이 끝나야 채점할 수 있습니다."
        )
    if any(
        {(r.get("id"), r.get("arm")) for r in rows} != expected_saved
        for rows in (traces, contexts)
    ):
        raise _fail("저장된 검색 결과의 ID/조건이 완전하지 않습니다.")
    (
        questions,
        references,
        evaluation_manifest,
        question_hash,
        evaluation_manifest_hash,
    ) = _load_evaluation(evaluation_dir)
    if retrieval_manifest.get("questions_sha256") != question_hash:
        raise _fail(
            "retrieval questions_sha256가 evaluation questions.json과 다릅니다."
        )
    if retrieval_manifest.get("corpus_sha256") != corpus_hash:
        raise _fail("retrieval corpus_sha256가 frozen corpus.sqlite3와 다릅니다.")
    vectors_manifest_hash = _sha256_bytes(vectors_manifest_bytes)
    if retrieval_manifest.get("vectors_manifest_sha256") != vectors_manifest_hash:
        raise _fail("retrieval vectors manifest hash가 index와 다릅니다.")
    if retrieval_manifest.get("vectors_sha256") != vectors_hash:
        raise _fail("retrieval vectors_sha256가 index와 다릅니다.")
    if retrieval_manifest.get("config") != vectors_manifest.get("config"):
        raise _fail("retrieval과 vectors manifest의 config가 다릅니다.")
    model_assets = retrieval_manifest.get("model_assets")
    if not isinstance(model_assets, dict) or set(model_assets) != {
        "embedding",
        "reranker",
    }:
        raise _fail("retrieval manifest의 model_assets 형식이 잘못됐습니다.")
    model_assets_hash = _object_sha(model_assets)
    code_hash = _runtime_fingerprint(
        retrieval_manifest.get("runtime_files"), retrieval_manifest.get("code_sha256")
    )
    manifest_ids = set(
        _validate_id_list(retrieval_manifest.get("question_ids"), "question_ids")
    )
    if manifest_ids != set(questions):
        raise _fail("retrieval manifest의 question_ids가 evaluation과 다릅니다.")
    manifest_arms = _validate_id_list(retrieval_manifest.get("arms"), "arms")
    if set(manifest_arms) != set(ARMS):
        raise _fail("retrieval manifest의 arms는 A/B/C여야 합니다.")

    pages, by_book_page, occurrences = _load_corpus(corpus_path)
    index_statistics = _load_index_statistics(corpus_dir, vectors_manifest, len(pages))
    _compile_references(references, by_book_page, pages)
    if retrieval_manifest.get("traces_sha256") != _sha256_file(
        retrieval_dir / "traces.jsonl"
    ):
        raise _fail("retrieval manifest의 traces_sha256가 traces.jsonl과 다릅니다.")
    if retrieval_manifest.get("contexts_sha256") != _sha256_file(
        retrieval_dir / "contexts.jsonl"
    ):
        raise _fail("retrieval manifest의 contexts_sha256가 contexts.jsonl과 다릅니다.")
    trace_map: dict[tuple[str, str], dict[str, Any]] = {}
    for trace in traces:
        question_id, arm = trace.get("id"), trace.get("arm")
        if (
            not isinstance(question_id, str)
            or question_id not in questions
            or arm not in ARMS
        ):
            raise _fail("retrieval trace에 evaluation 범위 밖 ID/arm이 있습니다.")
        key = (question_id, arm)
        if key in trace_map:
            raise _fail("retrieval traces.jsonl에 중복 ID/arm이 있습니다.")
        if trace.get("question_sha256") != _sha256_bytes(
            questions[question_id]["question"].encode("utf-8")
        ):
            raise _fail(
                "retrieval trace의 question_sha256가 questions.json과 다릅니다."
            )
        trace_map[key] = trace
    expected_keys = {(question_id, arm) for question_id in questions for arm in ARMS}
    if set(trace_map) != expected_keys:
        raise _fail("retrieval traces.jsonl이 모든 문항×A/B/C를 포함하지 않습니다.")
    if retrieval_manifest.get("trace_count") != len(expected_keys):
        raise _fail("retrieval manifest의 trace_count가 문항×A/B/C 수와 다릅니다.")

    context_map: dict[tuple[str, str], dict[str, Any]] = {}
    for context in contexts:
        question_id, arm = context.get("id"), context.get("arm")
        if (
            not isinstance(question_id, str)
            or question_id not in questions
            or arm not in ARMS
        ):
            raise _fail("contexts.jsonl에 evaluation 범위 밖 ID/arm이 있습니다.")
        key = (question_id, arm)
        if key in context_map:
            raise _fail("contexts.jsonl에 중복 ID/arm이 있습니다.")
        context_map[key] = context

    question_results: list[dict[str, Any]] = []
    for question_id in questions:
        reference = references[question_id]
        arm_scores = {
            arm: _question_score(
                reference,
                arm,
                trace_map[(question_id, arm)],
                context_map.get((question_id, arm)),
                pages,
                by_book_page,
                occurrences,
            )
            for arm in ARMS
        }
        question_results.append(
            {
                "id": question_id,
                "split": reference["split"],
                "category": reference["category"],
                "arms": arm_scores,
            }
        )

    summary = _summarize(question_results)
    summary["index"] = index_statistics
    summary["work"] = {}
    for split in ("development", "validation"):
        summary["work"][split] = {}
        for arm in ARMS:
            rows = [
                trace
                for (qid, condition), trace in trace_map.items()
                if condition == arm and references[qid]["split"] == split
            ]
            summary["work"][split][arm] = {
                "requests": len(rows),
                "query_embedding_calls": len(rows),
                "rerank_calls": len(rows),
                "external_api_calls": 0,
                "rerank_pairs": sum(r["work"]["rerank_pairs"] for r in rows),
                "rerank_tokens": sum(r["work"]["rerank_tokens"] for r in rows),
                "rerank_raw_tokens": sum(r["work"]["rerank_raw_tokens"] for r in rows),
                "rerank_truncated": sum(r["work"]["rerank_truncated"] for r in rows),
                "mean_candidate_pages": _mean(
                    [
                        len({p["source_id"] for p in r["pool"]})
                        if r.get("pool") is not None
                        else None
                        for r in rows
                    ]
                ),
                "mean_final_pages": _mean(
                    [
                        len(r["final_pages"])
                        if r.get("final_pages") is not None
                        else None
                        for r in rows
                    ]
                ),
                "mean_context_tokens": _mean([r["context_tokens"] for r in rows]),
                "relation_triggered": sum(
                    bool(r["relation"]["proposed_units"]) for r in rows
                ),
                "mean_displaced_units": _mean(
                    [len(r["relation_displaced_units"]) for r in rows]
                ),
            }
    result = {
        "schema_version": "pattern-retrieval-scores-v1",
        "inputs": {
            "scorer_sha256": _sha256_file(Path(__file__)),
            "questions_sha256": question_hash,
            "references_sha256": _sha256_bytes(
                (evaluation_dir / "references.json").read_bytes()
            ),
            "evaluation_manifest_sha256": evaluation_manifest_hash,
            "corpus_sha256": corpus_hash,
            "code_sha256": code_hash,
            "retrieval_manifest_sha256": _sha256_bytes(retrieval_manifest_bytes),
            "vectors_manifest_sha256": vectors_manifest_hash,
            "vectors_sha256": vectors_hash,
            "model_assets_sha256": model_assets_hash,
            "timing_ids": list(evaluation_manifest["timing_ids"]),
        },
        "summary": summary,
        "questions": question_results,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / "scores.json", result)
    return result
