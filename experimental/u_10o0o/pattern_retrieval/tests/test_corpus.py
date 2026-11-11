"""Focused tests for fresh PDF parsing and corpus integrity."""

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ingestion import layout
from experimental.u_10o0o.pattern_retrieval import corpus


def _page(*sections: str) -> str:
    return "\n\n".join(["Synthetic Pattern Book", *sections, "1 / 1"]) + "\n"


def _install_fake_pdftotext(monkeypatch, extracted_by_name: dict[str, str]) -> None:
    def fake_run(command, **kwargs):
        if command == ["pdftotext", "-v"]:
            return SimpleNamespace(
                returncode=0,
                stdout=b"pdftotext version synthetic",
                stderr=b"",
            )
        assert command[:2] == ["pdftotext", "-layout"]
        assert command[-1] == "-"
        name = Path(command[-2]).name
        return SimpleNamespace(
            returncode=0,
            stdout=extracted_by_name[name].encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(corpus.subprocess, "run", fake_run)


def _create_source_files(tmp_path: Path, names: tuple[str, ...]) -> Path:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    for name in names:
        (source_dir / name).write_bytes(f"synthetic bytes for {name}".encode())
    return source_dir


def test_build_corpus_preserves_source_tables_and_explicit_single_case_links(
    tmp_path, monkeypatch
):
    record_one = (
        "Case ID: GR26-001\n"
        "Booking Code: BK-12345\n"
        "Date: 2026-11-04 Amount: USD 1,234.50"
    )
    common_rule = "공통 규칙\n적용 조건과 예외는 원문 범위대로 확인한다."
    table_section = "Table status section"
    table_header = f"{'기록번호':<20}{'상태':<16}금액"
    table_row_one = (
        f"{'TC-001':<20}{'처리완료':<16}USD 1,234.50\n"
        f"{'':<20}{'추가설명':<16}2026-11-04"
    )
    table_row_two = f"{'TC-002':<20}{'확인필요':<16}2026-11-04"
    repeated_scope = "PRIVATE-RAW-SENTINEL: unresolved source scope"

    page_one = _page(
        f"Case section\n{record_one}",
        common_rule,
        table_section,
        table_header,
        table_row_one,
        table_row_two,
        repeated_scope,
    )
    page_two = _page(
        "Case section\nCase ID: GR26-002\nBooking Code: BK-22222",
        repeated_scope,
    )
    extracted = {
        "AS001.pdf": page_one + "\f",
        "AS002.pdf": page_two + "\f",
    }
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)

    messages = []
    output_dir = tmp_path / "index"
    manifest = corpus.build_corpus(source_dir, output_dir, messages.append)

    database = output_dir / "corpus.sqlite3"
    assert database.is_file()
    assert (output_dir / "corpus-manifest.json").is_file()
    assert manifest["source_count"] == 2
    assert manifest["counts"]["pages"] == 2
    assert manifest["integrity"] == {
        "sqlite": "ok",
        "foreign_keys": "ok",
        "page_units": True,
        "body_block_coverage": True,
        "source_pdf_hashes": "verified_before_after",
        "table_cell_values": True,
        "raw_table_rows": True,
        "code_links": True,
    }
    assert manifest["status"] == "complete"
    assert (
        manifest["corpus_sha256"] == hashlib.sha256(database.read_bytes()).hexdigest()
    )
    assert manifest["runtime"]["layout_sha256"]
    assert manifest["runtime"]["parser_sha256"]
    assert manifest["runtime"]["pdftotext_version"] == "pdftotext version synthetic"
    policy = manifest["code_link_policy"]
    assert policy["case_anchor"]["page_cooccurrence_is_anchor"] is False
    assert policy["case_anchor"]["rule"].startswith("One distinct complete code")
    assert (
        "booking/reservation" in policy["linked_identifier_labels"]["supported_roles"]
    )
    assert policy["excluded_identifier_labels"][
        "generic_number_or_reference_without_supported_role"
    ]
    assert (
        policy["case_anchor"]["structured_record_titles"]["slash_title_component_count"]
        == 4
    )
    assert policy["linked_identifier_labels"]["pure_role_label_allowed"] is True
    assert all("PRIVATE-RAW-SENTINEL" not in message for message in messages)
    assert "PRIVATE-RAW-SENTINEL" not in json.dumps(manifest)
    previous_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        corpus.build_corpus(source_dir, output_dir, messages.append)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == previous_hash

    with sqlite3.connect(database) as connection:
        page_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(pages)")
        ]
        unit_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(units)")
        ]
        occurrence_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(occurrences)")
        ]
        link_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(code_links)")
        ]
        assert page_columns == [
            "source_id",
            "book_id",
            "file_name",
            "page",
            "text",
            "heading",
            "pdf_sha256",
        ]
        assert unit_columns == ["unit_id", "arm", "kind", "text", "scope_key"]
        assert occurrence_columns == [
            "unit_id",
            "source_id",
            "line_start",
            "line_end",
            "case_id",
            "role",
            "identifiers_json",
        ]
        assert link_columns == [
            "code_norm",
            "code_type",
            "case_id",
            "source_id",
            "line_start",
            "line_end",
            "relation",
        ]

        page_rows = connection.execute(
            "SELECT source_id, book_id, text, pdf_sha256 FROM pages ORDER BY source_id"
        ).fetchall()
        assert page_rows == [
            (
                "AS001:p0001",
                "AS001",
                page_one,
                hashlib.sha256((source_dir / "AS001.pdf").read_bytes()).hexdigest(),
            ),
            (
                "AS002:p0001",
                "AS002",
                page_two,
                hashlib.sha256((source_dir / "AS002.pdf").read_bytes()).hexdigest(),
            ),
        ]

        links = connection.execute(
            """
            SELECT code_norm, case_id, source_id, relation
            FROM code_links
            ORDER BY code_norm
            """
        ).fetchall()
        assert links == [
            (
                "BK-12345",
                "GR26-001",
                "AS001:p0001",
                "explicit_field_code_to_case_record",
            ),
            (
                "BK-22222",
                "GR26-002",
                "AS002:p0001",
                "explicit_field_code_to_case_record",
            ),
            (
                "GR26-001",
                "GR26-001",
                "AS001:p0001",
                "explicit_case_identifier_to_self",
            ),
            (
                "GR26-002",
                "GR26-002",
                "AS002:p0001",
                "explicit_case_identifier_to_self",
            ),
        ]

        kinds = connection.execute(
            "SELECT kind, count(*) FROM units WHERE arm = 'B' GROUP BY kind"
        ).fetchall()
        assert dict(kinds)["common_rule"] == 1
        repeated = connection.execute(
            """
            SELECT count(*) FROM units
            WHERE arm = 'B' AND text LIKE '%PRIVATE-RAW-SENTINEL%'
            """
        ).fetchone()[0]
        assert repeated == 2

        table_units = [
            row[0]
            for row in connection.execute(
                """
                SELECT text FROM units
                WHERE arm = 'B' AND
                    (text LIKE '%TC-001%' OR text LIKE '%TC-002%')
                ORDER BY unit_id
                """
            )
        ]
        assert len(table_units) == 2
        table_unit = "\n".join(table_units)
        for raw_value in ("TC-001", "TC-002", "USD 1,234.50", "2026-11-04"):
            assert raw_value in table_unit
        assert table_units[0].count("USD 1,234.50") == 1
        assert "상태: 처리완료 추가설명" in table_units[0]
        assert "금액: USD 1,234.50 2026-11-04" in table_units[0]
        assert table_row_one not in table_unit
        assert table_row_two not in table_unit
        assert table_row_one in page_one
        table_span = connection.execute(
            """
            SELECT o.line_start, o.line_end
            FROM occurrences AS o
            JOIN units AS u ON u.unit_id = o.unit_id
            WHERE u.arm = 'B' AND u.text LIKE '%TC-001%'
            """
        ).fetchone()
        expected_start = page_one.splitlines().index(table_row_one.splitlines()[0]) + 1
        assert table_span == (
            expected_start,
            expected_start + len(table_row_one.splitlines()) - 1,
        )
        table_role = connection.execute(
            """
            SELECT o.role FROM occurrences AS o
            JOIN units AS u ON u.unit_id = o.unit_id
            WHERE u.arm = 'B' AND u.text LIKE '%TC-001%'
            """
        ).fetchone()[0]
        assert "Table status section" in table_role
        heading = connection.execute(
            "SELECT heading FROM pages WHERE source_id = 'AS001:p0001'"
        ).fetchone()[0]
        assert heading == "Synthetic Pattern Book\nCase section"

        body_heading, body_blocks = layout.page_body(page_one, "AS001", 1, 1)
        assert body_heading
        for block in body_blocks:
            covered = connection.execute(
                """
                SELECT count(*) FROM occurrences AS o
                JOIN units AS u ON u.unit_id = o.unit_id
                WHERE o.source_id = 'AS001:p0001'
                  AND u.arm = 'B'
                  AND o.line_start <= ?
                  AND o.line_end >= ?
                """,
                (block["start"], block["end"]),
            ).fetchone()[0]
            assert covered == 1

        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_multiline_table_record_links_use_full_logical_row_span(tmp_path, monkeypatch):
    header = f"{'처리기록':<16}{'사건번호':<16}예약번호"
    row = f"{'R-050':<16}{'HG2026-0050':<16}BK-5050\n{'':<16}{'':<16}추가 설명"
    page = _page("Record section\nCase section", header, row)
    extracted = {"AS050.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    manifest = corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    expected_start = page.splitlines().index(row.splitlines()[0]) + 1
    expected_end = expected_start + len(row.splitlines()) - 1
    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        source_page = connection.execute(
            "SELECT text FROM pages WHERE source_id = 'AS050:p0001'"
        ).fetchone()[0]
        assert source_page == page
        record = connection.execute(
            """
            SELECT u.kind, u.text, o.line_start, o.line_end, o.case_id
            FROM units AS u
            JOIN occurrences AS o ON o.unit_id = u.unit_id
            WHERE u.arm = 'B' AND u.text LIKE '%HG2026-0050%'
            """
        ).fetchone()
        assert record[0] == "record"
        assert "사건번호: HG2026-0050" in record[1]
        assert "예약번호: BK-5050 추가 설명" in record[1]
        assert record[2:4] == (expected_start, expected_end)
        assert record[4] == "HG2026-0050"

        links = connection.execute(
            """
            SELECT code_norm, case_id, line_start, line_end, relation
            FROM code_links
            ORDER BY code_norm
            """
        ).fetchall()
        assert links == [
            (
                "BK-5050",
                "HG2026-0050",
                expected_start,
                expected_end,
                "explicit_field_code_to_case_record",
            ),
            (
                "HG2026-0050",
                "HG2026-0050",
                expected_start,
                expected_end,
                "explicit_case_identifier_to_self",
            ),
        ]
    assert manifest["integrity"]["body_block_coverage"] is True
    assert manifest["integrity"]["table_cell_values"] is True
    assert manifest["integrity"]["raw_table_rows"] is True


def test_structured_title_record_page_contains_record_table_and_typed_code_links(
    tmp_path, monkeypatch
):
    title = "HG2026-0050 / source descriptor / BK-5050 / 현장 사건 수신대장"
    header = f"{'처리기록':<16}{'사건번호':<16}증빙"
    row = f"{'R-050':<16}{'HG2026-0050':<16}EV-5050\n{'':<16}{'':<16}추가 설명"
    page = _page(title, "Evidence section", header, row)
    extracted = {"AS050.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    manifest = corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    title_line = page.splitlines().index(title) + 1
    row_start = page.splitlines().index(row.splitlines()[0]) + 1
    row_end = row_start + len(row.splitlines()) - 1
    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        assert connection.execute(
            "SELECT text FROM pages WHERE source_id = 'AS050:p0001'"
        ).fetchone() == (page,)
        record = connection.execute(
            """
            SELECT u.kind, u.text, o.case_id, o.role, o.line_start, o.line_end
            FROM units AS u
            JOIN occurrences AS o ON o.unit_id = u.unit_id
            WHERE u.unit_id = 'B:AS050:p0001:record'
            """
        ).fetchone()
        assert record[0] == "record"
        assert "사건번호: HG2026-0050" in record[1]
        assert "증빙: EV-5050 추가 설명" in record[1]
        assert record[2:] == (
            "HG2026-0050",
            "현장 사건 수신대장",
            1,
            len(page.splitlines()),
        )
        assert (
            connection.execute(
                """
            SELECT count(*) FROM units AS u
            JOIN occurrences AS o ON o.unit_id = u.unit_id
            WHERE u.arm = 'B' AND o.source_id = 'AS050:p0001'
            """
            ).fetchone()[0]
            == 1
        )
        links = connection.execute(
            """
            SELECT code_norm, case_id, line_start, line_end, relation
            FROM code_links WHERE source_id = 'AS050:p0001'
            ORDER BY code_norm, relation
            """
        ).fetchall()
        assert links == [
            (
                "EV-5050",
                "HG2026-0050",
                row_start,
                row_end,
                "explicit_field_code_to_case_record",
            ),
            (
                "HG2026-0050",
                "HG2026-0050",
                row_start,
                row_end,
                "explicit_case_identifier_to_self",
            ),
            (
                "HG2026-0050",
                "HG2026-0050",
                title_line,
                title_line,
                "explicit_structured_record_title_case_to_self",
            ),
        ]
        assert "BK-5050" not in {
            row[0]
            for row in connection.execute(
                "SELECT code_norm FROM code_links WHERE source_id = 'AS050:p0001'"
            )
        }
    assert manifest["counts"]["record_pages"] == 1
    assert manifest["counts"]["record_scope_slash_title"] == 1
    assert manifest["integrity"]["body_block_coverage"] is True
    assert manifest["integrity"]["code_links"] is True


def test_typed_document_heading_record_uses_second_block_case_code(
    tmp_path, monkeypatch
):
    title = "Typed document heading"
    case_id = "HA28-000060"
    page = (
        f"{title}\n\n{case_id}\nmetadata with no case reference\n\n"
        "Body section\n\nStatus: confirmed\n\n1 / 1\n"
    )
    extracted = {"AS060.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    manifest = corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    code_line = page.splitlines().index(case_id) + 1
    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        record = connection.execute(
            """
            SELECT u.kind, o.case_id, o.role, o.line_start, o.line_end
            FROM units AS u JOIN occurrences AS o ON o.unit_id = u.unit_id
            WHERE u.unit_id = 'B:AS060:p0001:record'
            """
        ).fetchone()
        assert record == (
            "record",
            case_id,
            title,
            1,
            len(page.splitlines()),
        )
        assert connection.execute(
            """
            SELECT code_norm, case_id, line_start, line_end, relation
            FROM code_links
            """
        ).fetchall() == [
            (
                case_id,
                case_id,
                code_line,
                code_line,
                "explicit_structured_record_title_case_to_self",
            )
        ]
    assert manifest["counts"]["record_scope_typed_document_heading"] == 1


def test_structured_record_title_metadata_links_only_typed_role_codes(
    tmp_path, monkeypatch
):
    case_id = "HG2026-0051"
    title = f"{case_id} / source descriptor / ZZ-5051 / 현장 사건 수신대장"
    metadata = (
        "현장 기록: metadata\n"
        "장비\nEQ-5051\n"
        "계약 번호: CN-5051\n"
        "증빙 EV-5051\n"
        "예약: BK-5051\n"
        "화주 번호: ORG-5051\n"
        "장비 종류: TYPE-5051\n"
        "Shipper equipment number: EQ-5053\n"
        "Internal review found equipment number: EQ-5054\n"
        "장비 번호: EQ-5052 (shipper reference)"
    )
    page = _page(title + "\n" + metadata, "Evidence section\nStatus confirmed")
    extracted = {"AS051.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    manifest = corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    metadata_line_start = page.splitlines().index("현장 기록: metadata") + 1
    metadata_line_end = (
        page.splitlines().index("장비 번호: EQ-5052 (shipper reference)") + 1
    )
    title_line = page.splitlines().index(title) + 1
    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        links = connection.execute(
            """
            SELECT code_norm, case_id, line_start, line_end, relation
            FROM code_links WHERE source_id = 'AS051:p0001'
            ORDER BY code_norm, relation
            """
        ).fetchall()
        assert links == [
            (
                "BK-5051",
                case_id,
                metadata_line_start,
                metadata_line_end,
                "explicit_field_code_to_case_record",
            ),
            (
                "CN-5051",
                case_id,
                metadata_line_start,
                metadata_line_end,
                "explicit_field_code_to_case_record",
            ),
            (
                "EQ-5051",
                case_id,
                metadata_line_start,
                metadata_line_end,
                "explicit_field_code_to_case_record",
            ),
            (
                "EQ-5052",
                case_id,
                metadata_line_start,
                metadata_line_end,
                "explicit_field_code_to_case_record",
            ),
            (
                "EV-5051",
                case_id,
                metadata_line_start,
                metadata_line_end,
                "explicit_field_code_to_case_record",
            ),
            (
                case_id,
                case_id,
                title_line,
                title_line,
                "explicit_structured_record_title_case_to_self",
            ),
        ]
    assert manifest["counts"]["record_metadata_typed_fields"] == 5


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("equipment number", True),
        ("예약 / 장비", True),
        ("증빙 코드", True),
        ("Shipper equipment number", False),
        ("Internal review found equipment number", False),
        ("00_kst이다_장비번호_오기_정정", False),
        ("장비 종류", False),
    ],
)
def test_code_role_labels_must_be_exact_supported_fields(label, expected):
    assert corpus._is_linkable_code_label(label) is expected


def test_record_metadata_labels_require_exact_semantic_field_boundaries():
    title = ("HG2026-0052", "현장 사건 수신대장", 1, 1, "slash_title")
    text = (
        "HG2026-0052 / source descriptor / ZZ-5052 / 현장 사건 수신대장\n"
        "Shipper equipment number: EQ-5053\n"
        "Internal review found equipment number: EQ-5054\n"
        "00_kst이다_장비번호_오기_정정: EQ-5055\n"
        "장비 번호: EQ-5052 (shipper reference)"
    )

    assert corpus._record_metadata_fields(text, title) == [
        ("장비 번호", "EQ-5052", "장비_번호")
    ]


def test_generic_typed_heading_with_other_case_family_is_not_record_scoped(
    tmp_path, monkeypatch
):
    case_id = "GR26-0080"
    page = (
        "Generic document heading\n\n"
        f"{case_id}\nmetadata block\n\n"
        "Body section\n\nStatus confirmed\n\n1 / 1\n"
    )
    extracted = {"AS080.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    manifest = corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM units WHERE unit_id = 'B:AS080:p0001:record'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM code_links WHERE relation = "
                "'explicit_structured_record_title_case_to_self'"
            ).fetchone()[0]
            == 0
        )
    assert manifest["counts"].get("record_pages", 0) == 0


def test_structured_title_with_competing_body_case_falls_back_to_local_scope(
    tmp_path, monkeypatch
):
    title = "HG2026-0070 / source descriptor / BK-7070 / 비용 청구 명세"
    record = "Case ID: HG2026-0071\nEvidence Code: EV-7071"
    page = _page(title, record)
    extracted = {"AS070.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    manifest = corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM units WHERE unit_id = 'B:AS070:p0001:record'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM code_links WHERE code_norm = 'HG2026-0070'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM code_links WHERE code_norm = 'EV-7071' AND case_id = 'HG2026-0071'"
            ).fetchone()[0]
            == 1
        )
    assert manifest["counts"]["record_scope_conflicts"] == 1


def test_ambiguous_case_scope_and_unlabelled_codes_do_not_create_links(
    tmp_path, monkeypatch
):
    ambiguous_block = "Case ID: GR26-003\nCase ID: GR27-009\nBooking Code: BK-30001"
    raw_block = "Unscoped reference BK-39999 remains source text."
    page = _page("Cases\n" + ambiguous_block, raw_block)
    extracted = {"AS003.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)

    output_dir = tmp_path / "index"
    corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM code_links").fetchone()[0] == 0
        ambiguous = connection.execute(
            """
            SELECT u.kind, u.text, o.case_id
            FROM units AS u
            JOIN occurrences AS o ON o.unit_id = u.unit_id
            WHERE u.arm = 'B' AND u.text LIKE '%BK-30001%'
            """
        ).fetchone()
        assert ambiguous[0] == "raw"
        assert "Case ID: GR26-003" in ambiguous[1]
        assert ambiguous[2] is None

        unscoped = connection.execute(
            """
            SELECT u.kind, u.text, o.case_id
            FROM units AS u
            JOIN occurrences AS o ON o.unit_id = u.unit_id
            WHERE u.arm = 'B' AND u.text LIKE '%Unscoped reference%'
            """
        ).fetchone()
        assert unscoped[0] == "raw"
        assert unscoped[1].endswith(raw_block + "\n")
        assert unscoped[2] is None


def test_common_rule_dedup_requires_same_explicit_scope_effect_and_exception(
    tmp_path, monkeypatch
):
    strict_rule = "공통 규칙\n적용 범위는 모든 기록이다. 효력은 계속된다. 예외는 없다."
    unscoped_rule = "공통 규칙\n효력은 계속된다. 예외는 원문 조건을 따른다."
    extracted = {
        "AS010.pdf": _page("Common section\n" + strict_rule + "\n\n" + unscoped_rule)
        + "\f",
        "AS011.pdf": _page("Common section\n" + strict_rule + "\n\n" + unscoped_rule)
        + "\f",
    }
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    manifest = corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        common_units = connection.execute(
            "SELECT count(*) FROM units WHERE arm = 'B' AND kind = 'common_rule'"
        ).fetchone()[0]
        common_occurrences = connection.execute(
            """
            SELECT count(*) FROM occurrences AS o
            JOIN units AS u ON u.unit_id = o.unit_id
            WHERE u.kind = 'common_rule'
            """
        ).fetchone()[0]
        repeated_groups = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT unit_id FROM occurrences
                GROUP BY unit_id HAVING count(*) > 1
            )
            """
        ).fetchone()[0]

    assert common_units == 3
    assert common_occurrences == 4
    assert repeated_groups == 1
    assert manifest["counts"]["common_deduplicated_unit_groups"] == 1
    assert manifest["counts"]["common_deduplicated_occurrences"] == 1


def test_source_pdf_hash_change_during_extraction_aborts_publish(tmp_path, monkeypatch):
    page = _page("Case section\nCase ID: GR26-100")
    extracted = {"AS100.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    source_file = source_dir / "AS100.pdf"
    changed = False

    def fake_run(command, **kwargs):
        nonlocal changed
        if command == ["pdftotext", "-v"]:
            return SimpleNamespace(
                returncode=0,
                stdout=b"pdftotext version synthetic",
                stderr=b"",
            )
        if not changed:
            source_file.write_bytes(b"changed during extraction")
            changed = True
        return SimpleNamespace(
            returncode=0,
            stdout=extracted[source_file.name].encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(corpus.subprocess, "run", fake_run)
    output_dir = tmp_path / "index"

    with pytest.raises(corpus.CorpusBuildError, match="changed during extraction"):
        corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    assert not (output_dir / "corpus.sqlite3").exists()
    assert not (output_dir / "corpus-manifest.json").exists()
    assert list(output_dir.glob(".corpus-*.sqlite3")) == []


def test_booking_role_extracts_one_canonical_anchor_from_a_multi_code_cell(
    tmp_path, monkeypatch
):
    header = f"{'예약·장비':<28}상태"
    row = f"{'GR26-080 / HL-080':<28}확인"
    page = _page("Record section", header, row)
    extracted = {"AS080.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        record = connection.execute(
            """
            SELECT u.kind, o.case_id FROM units AS u
            JOIN occurrences AS o ON o.unit_id = u.unit_id
            WHERE u.arm = 'B' AND u.text LIKE '%GR26-080%'
            """
        ).fetchone()
        assert record == ("record", "GR26-080")
        assert connection.execute(
            """
            SELECT code_norm, case_id, relation FROM code_links
            ORDER BY code_norm
            """
        ).fetchall() == [
            ("GR26-080", "GR26-080", "explicit_booking_key_to_self"),
            ("HL-080", "GR26-080", "explicit_field_code_to_case_record"),
        ]


def test_reference_code_and_case_type_do_not_anchor_booking_number(
    tmp_path, monkeypatch
):
    reference_block = "Record section\nReference: HA28-000001\nReservation No: BK-9001"
    type_block = "사건 유형: GR26-000002\n예약번호: BK-9002"
    canonical_booking = "예약번호: HA28-000003"
    extracted = {
        "AS020.pdf": _page(reference_block) + "\f",
        "AS021.pdf": _page("Record section\n" + canonical_booking) + "\f",
        "AS022.pdf": _page("Record section\n" + type_block) + "\f",
    }
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        links = connection.execute(
            "SELECT code_norm, case_id, source_id FROM code_links"
        ).fetchall()
        assert links == [("HA28-000003", "HA28-000003", "AS021:p0001")]

        page_hints = connection.execute(
            """
            SELECT source_id, case_id FROM occurrences AS o
            JOIN units AS u ON u.unit_id = o.unit_id
            WHERE u.arm = 'A'
            ORDER BY source_id
            """
        ).fetchall()
        assert page_hints == [
            ("AS020:p0001", None),
            ("AS021:p0001", "HA28-000003"),
            ("AS022:p0001", None),
        ]

        unsafe_rows = connection.execute(
            """
            SELECT o.case_id FROM occurrences AS o
            JOIN units AS u ON u.unit_id = o.unit_id
            WHERE u.arm = 'B'
              AND (u.text LIKE '%HA28-000001%' OR u.text LIKE '%GR26-000002%')
            ORDER BY o.source_id
            """
        ).fetchall()
        assert unsafe_rows == [(None,), (None,)]


def test_explicit_booking_column_can_anchor_a_canonical_case_without_id_suffix(
    tmp_path, monkeypatch
):
    header = f"{'처리기록':<16}{'예약':<16}상태"
    row = f"{'R-060':<16}{'HA28-000060':<16}검토"
    page = _page("Reservation section", header, row)
    extracted = {"AS060.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        record = connection.execute(
            """
            SELECT u.kind, o.case_id FROM units AS u
            JOIN occurrences AS o ON o.unit_id = u.unit_id
            WHERE u.arm = 'B' AND u.text LIKE '%HA28-000060%'
            """
        ).fetchone()
        assert record == ("record", "HA28-000060")
        assert connection.execute(
            """
            SELECT case_id FROM occurrences AS o
            JOIN units AS u ON u.unit_id = o.unit_id
            WHERE u.arm = 'A'
            """
        ).fetchone() == ("HA28-000060",)
        assert connection.execute(
            """
            SELECT code_norm, case_id, relation FROM code_links
            """
        ).fetchall() == [
            (
                "HA28-000060",
                "HA28-000060",
                "explicit_booking_key_to_self",
            )
        ]


def test_party_alias_fields_are_preserved_but_not_linked_to_case_codes(
    tmp_path, monkeypatch
):
    header = f"{'기록번호':<16}{'별칭':<18}{'업체명':<16}상태"
    row = f"{'GR26-030':<16}{'North Bay 21':<18}{'North Bay':<16}확인"
    page = _page(
        "Record section\nReference: GR26-030",
        "Party alias section",
        header,
        row,
    )
    extracted = {"AS030.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    manifest = corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    assert manifest["alias_handling"] == {
        "status": "preserved_unlinked_party_aliases",
        "source_alias_tables": 1,
        "source_alias_cells": 1,
        "source_alias_paragraphs": 0,
        "party_context_tables": 1,
        "alias_values_matching_complete_code": 0,
        "registered_case_alias_links": 0,
        "reason": (
            "Source alias fields appear in party or organization tables. "
            "Their values are preserved as source field text and are not "
            "registered as case codes."
        ),
    }
    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM code_links").fetchone()[0] == 0
        alias_unit = connection.execute(
            """
            SELECT u.text, o.case_id FROM units AS u
            JOIN occurrences AS o ON o.unit_id = u.unit_id
            WHERE u.arm = 'B' AND u.text LIKE '%North Bay 21%'
            """
        ).fetchone()
        assert "별칭: North Bay 21" in alias_unit[0]
        assert alias_unit[1] is None
        assert (
            connection.execute(
                "SELECT text FROM pages WHERE source_id = 'AS030:p0001'"
            ).fetchone()[0]
            == page
        )


def test_party_number_is_not_a_case_link_even_with_explicit_case_anchor(
    tmp_path, monkeypatch
):
    page = _page("Record section\nCase ID: GR26-040\nCompany Number: ORG-0001")
    extracted = {"AS040.pdf": page + "\f"}
    source_dir = _create_source_files(tmp_path, tuple(extracted))
    _install_fake_pdftotext(monkeypatch, extracted)
    output_dir = tmp_path / "index"

    corpus.build_corpus(source_dir, output_dir, lambda _message: None)

    with sqlite3.connect(output_dir / "corpus.sqlite3") as connection:
        links = connection.execute(
            "SELECT code_norm, case_id, relation FROM code_links"
        ).fetchall()
        assert links == [("GR26-040", "GR26-040", "explicit_case_identifier_to_self")]
        identifiers = connection.execute(
            """
            SELECT identifiers_json FROM occurrences AS o
            JOIN units AS u ON u.unit_id = o.unit_id
            WHERE u.arm = 'B'
            """
        ).fetchone()[0]
        assert "ORG-0001" in identifiers
        assert "ORG-0001" not in {
            row[0] for row in connection.execute("SELECT code_norm FROM code_links")
        }
