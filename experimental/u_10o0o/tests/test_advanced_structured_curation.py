import json
from hashlib import sha256
import pytest

from app.ingestion.curation import write_rows
from app.ingestion.layout import blocks, header_cells, looks_like_row, parse_table
from experimental.u_10o0o import advanced_structured_curation as structured


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _sha(path):
    return sha256(path.read_bytes()).hexdigest()


def _table_fixture():
    source_id = "advanced:AS001:p1"
    header = "항목          비용"
    row1 = "보관료        500원"
    row2 = "지연료        300원"
    layout = "IS01 | 문서 제목\n\n제2절 비용\n\n" + header + "\n\n" + row1 + "\n\n" + row2 + "\n\n각주는 그대로 둔다\n\n1/1"
    parts = blocks(layout)
    header_index = next(index for index, part in enumerate(parts) if header_cells(part))
    end = header_index + 1
    while end < len(parts) and looks_like_row(parts[end]) and not header_cells(parts[end]):
        end += 1
    parsed = parse_table(parts[header_index], parts[header_index + 1 : end])
    records = [
        {
            "source_id": source_id,
            "kind": "table_row",
            "review_status": "parsed",
            "line_start": part["start"],
            "line_end": part["end"],
            "section": "제2절 비용",
            "fields": row,
        }
        for part, row in zip(parts[header_index + 1 : end], parsed, strict=True)
    ]
    base_text = f"{header}\n{row1}\n{row2}\n\n각주는 그대로 둔다"
    page = {
        "file_name": "AS001.pdf",
        "page": 1,
        "text": layout,
    }
    source = {"source_id": source_id, "heading": "IS01 | 문서 제목 > 제2절 비용"}
    base_input = {"text": base_text, "removed_blocks": [0, 1, len(parts) - 1]}
    return page, source, records, base_input, layout


def test_render_verified_is_table_keeps_header_order_and_surrounding_text():
    page, source, records, base_input, layout = _table_fixture()

    rendered, details = structured._render_verified_tables(
        page, source, records, base_input, layout
    )

    assert details == {"status": "rendered", "tables": 1, "rows": 2}
    assert rendered.startswith("IS01 | 문서 제목 > 제2절 비용")
    first_label = rendered.index("항목: 보관료")
    second_label = rendered.index("비용: 500원")
    assert first_label < second_label
    assert "제2절 비용" in rendered
    assert "각주는 그대로 둔다" in rendered
    assert "보관료        500원" not in rendered


def test_uncertain_is_table_record_falls_back_to_baseline_text():
    page, source, records, base_input, layout = _table_fixture()
    records[0]["fields"]["비용"] = "다른 값"

    rendered, details = structured._render_verified_tables(
        page, source, records, base_input, layout
    )

    assert rendered == base_input["text"]
    assert details["status"] == "table_record_mismatch"


def test_hg_rule_snapshot_indexes_only_exact_repeated_source_blocks(tmp_path, monkeypatch):
    curated = tmp_path / "curated"
    case_index = tmp_path / "case-index"
    curated.mkdir()
    case_index.mkdir()
    rule_text = "보관료는 연결된 계약의 조건으로 적용한다."
    sources = []
    cases = ["HG2026-0001", "HG2026-0002"]
    pages = []
    links = []
    for index, case_id in enumerate(cases, 1):
        book_id = f"AS00{index}"
        source_id = f"advanced:{book_id}:p1"
        file_sha = f"{index:064x}"
        heading = f"{case_id} | 비용 조건 약정"
        sources.append(
            {
                "source_id": source_id,
                "dataset": "advanced",
                "book_id": book_id,
                "file_name": f"{book_id}.pdf",
                "file_sha256": file_sha,
                "page": 1,
                "heading": heading,
                "sections": [],
            }
        )
        pages.append(
            {
                "file_name": f"{book_id}.pdf",
                "page": 1,
                "text": f"{heading}\n\n{rule_text}\n\n1/1",
            }
        )
        links.append(
            {
                "case_id": case_id,
                "record_role": structured.ROLES[0],
                "source_id": source_id,
                "match_rule": "page_heading_case_id",
            }
        )
    rules = [
        {
            "rule_id": "rule-1",
            "text": rule_text,
            "kind": "business_rule",
            "scope": "linked_source_context",
            "review_status": "source_matched",
            "trust": "retrieved_evidence",
            "sources": [row["source_id"] for row in sources],
        }
    ]
    rules_path, sources_path = curated / "rules.jsonl", curated / "sources.jsonl"
    write_rows(rules_path, rules)
    write_rows(sources_path, sources)
    manifest = {
        "identity": {"extractor": "fixture", "documents": []},
        "outputs": {
            "rules.jsonl": _sha(rules_path),
            "sources.jsonl": _sha(sources_path),
        },
    }
    manifest_path = curated / "manifest.json"
    _write_json(manifest_path, manifest)
    case_path = case_index / "case_sources.jsonl"
    write_rows(case_path, links)
    _write_json(
        case_index / "summary.json",
        {
            "curation_manifest_sha256": _sha(manifest_path),
            "case_sources_sha256": _sha(case_path),
        },
    )
    monkeypatch.setattr(structured, "build_index", lambda _: (links, [], []))
    output = tmp_path / "snapshot"

    structured.build_hg_rule_occurrence_snapshot(
        output, pages, curated=curated, case_index=case_index
    )
    index = structured.load_hg_rule_index(output, pages)
    match = index.match(
        "보관료는 연결된 계약의 조건으로 적용한다",
        [cases[0]],
        requested_roles=[structured.ROLES[0]],
    )

    assert match.status == "matched"
    assert match.rule_ids == ("rule-1",)
    assert len(match.occurrences) == 1
    assert match.occurrences[0].case_id == cases[0]
    assert match.occurrences[0].page is pages[0]
    assert index.counts["occurrences"] == 2


def test_hg_rule_match_requires_verified_case_and_scope(tmp_path):
    page = {"file_name": "AS001.pdf", "page": 1, "text": "source"}
    rule = {
        "rule_id": "rule-1",
        "text": "보관료는 계약 조건으로 정한다",
        "kind": "business_rule",
        "scope": "linked_source_context",
        "review_status": "source_matched",
        "trust": "retrieved_evidence",
    }
    occurrence = structured.RuleOccurrence(
        "rule-1", "advanced:AS001:p1", "HG2026-0001", structured.ROLES[0], 1, 1, page
    )
    index = structured.HGRuleOccurrenceIndex([rule], [occurrence])

    assert index.match("보관료 계약 조건", []).status == "no_verified_case"
    assert index.match("보관료 계약 조건", ["HG2026-0001"]).status == "matched"
    with pytest.raises(ValueError):
        structured.HGRuleOccurrenceIndex(
            [rule | {"scope": "global"}], [occurrence]
        ).match("보관료 계약 조건", ["HG2026-0001"])
