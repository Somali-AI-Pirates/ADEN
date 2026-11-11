from __future__ import annotations

import hashlib
import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from experimental.u_10o0o.fresh_record_hybrid.units import (  # noqa: E402
    build_artifacts,
    event_id_for,
    normalize_code,
    read_pages,
)


def make_page(source_id: str, lines: list[str]) -> dict[str, object]:
    return {
        "source_id": source_id,
        "book_id": source_id.split(":")[0],
        "page": int(source_id.split(":")[1]),
        "text": "\n".join(lines),
        "lines": lines,
    }


def lines_covered(unit: dict[str, object]) -> list[tuple[str, int, str]]:
    text = str(unit["text"]).splitlines()
    spans = unit["line_spans"]
    result = []
    cursor = 0
    for span in spans:
        for number in range(span["start"], span["end"] + 1):
            result.append((span["source_id"], number, text[cursor]))
            cursor += 1
    if cursor != len(text):
        raise AssertionError("unit text and line spans do not describe the same lines")
    return result


class UnitsTests(unittest.TestCase):
    def test_code_normalization_only_drops_separators(self) -> None:
        self.assertEqual(normalize_code("SW28-A041"), "SW28A041")
        self.assertEqual(normalize_code("SW 28/A-041"), "SW28A041")
        self.assertNotEqual(normalize_code("SW28-A041"), normalize_code("SW28-A042"))
        self.assertNotEqual(normalize_code("gr26-p0554"), normalize_code("GR26-P0554"))
        self.assertEqual(event_id_for("GR26-P0554"), "event:GR26P0554")

    def test_hg_record_links_only_explicit_fields(self) -> None:
        lines = [
            "수입 운송·비용 원문 인계철 / HS-057",
            "",
            "HG2029-00012 / 나루기전 / HL-40GP-00012 / 현장 사건 수신대장",
            "대조 기준 2029-05-24. 장비 HL-40GP-00012(40GP), 계약 CT-00012.",
            "다른 견적에는 20GP 공통 유형이 언급되지만 예약 대상은 아니다.",
            "Unlabeled mention: CT-99999 and 20GP are shown together.",
            "",
            "사건·예정·등록 구분",
            "",
            "항목                        날짜 또는 상태                   증빙",
            "",
            "실제 양하                     2029-05-05                 EV-SH-00012-D / 확인",
            "",
            "실제 적입 장비 반출               2029-05-14                 EV-SH-00012-G / 확인",
            "",
            "실제 지정 장치장 반납              2029-05-21                 EV-SH-00012-R",
        ]
        units, index, _ = build_artifacts([make_page("AS057:001", lines)])
        b_unit = next(unit for unit in units if unit["corpus"] == "B")
        event_id = "event:HG202900012"
        event = index["events"][event_id]
        self.assertEqual(event["unit_ids"], [b_unit["unit_id"]])
        self.assertEqual(index["code_to_events"]["HL40GP00012"], [event_id])
        self.assertEqual(index["code_to_events"]["CT00012"], [event_id])
        self.assertEqual(index["code_to_events"]["EVSH00012D"], [event_id])
        self.assertNotIn("20GP", index["code_to_events"])
        self.assertNotIn("CT99999", index["code_to_events"])
        roles = {relation["role"] for relation in event["relations"]}
        self.assertIn("reservation_key", roles)
        self.assertIn("equipment", roles)
        self.assertIn("contract_reference", roles)
        self.assertTrue(
            all(relation["source_id"] == "AS057:001" for relation in event["relations"])
        )

    def test_shared_type_and_suffix_do_not_merge_hg_events(self) -> None:
        pages = []
        for serial in (12, 13):
            hg = f"HG2029-{serial:05d}"
            hl = f"HL-40GP-{serial:05d}"
            pages.append(
                make_page(
                    f"AS057:{serial:03d}",
                    [
                        "수입 운송·비용 원문 인계철 / HS-057",
                        "",
                        f"{hg} / 화주 / {hl} / 현장 사건 수신대장",
                        f"장비 {hl}(40GP). 계약 CT-{serial:05d}.",
                    ],
                )
            )
        units, index, _ = build_artifacts(pages)
        first_id = "event:HG202900012"
        second_id = "event:HG202900013"
        self.assertEqual(index["code_to_events"]["HG202900012"], [first_id])
        self.assertEqual(index["code_to_events"]["HG202900013"], [second_id])
        self.assertEqual(index["code_to_events"]["HL40GP00012"], [first_id])
        self.assertEqual(index["code_to_events"]["HL40GP00013"], [second_id])
        self.assertNotIn("40GP", index["code_to_events"])
        self.assertNotIn("00012", index["code_to_events"])
        self.assertNotEqual(
            index["events"][first_id]["unit_ids"],
            index["events"][second_id]["unit_ids"],
        )

    def test_is_rows_keep_wrapped_values_and_narrative_separate(self) -> None:
        lines = [
            "IS01 | 업무조건 담당",
            "",
            "서비스 조건 자료집",
            "",
            "서비스 범위",
            "",
            "서비스 범위 / 접수 항목 외 2건",
            "",
            "도입 설명은 원문 상태로 유지합니다.",
            "이 문장은 앞 문장의 이어쓰기입니다.",
            "",
            "서비스 범위 — 대상별 처리 원문",
            "",
            "기록번호          예약번호          처리상태",
            "",
            "IS01-001-01       GR27-F09105      최초확정",
            "                   설명 셀의 두 번째 줄 40GP",
            "",
            "IS01-001-02       GR27-F09106      검토 중",
            "                   별도 상태 행 20GP",
            "",
            "마무리 설명도 별도 단위입니다.",
            "",
            "1 / 150",
        ]
        units, index, stats = build_artifacts([make_page("AS007:001", lines)])
        b_units = [unit for unit in units if unit["corpus"] == "B"]
        row_units = [unit for unit in b_units if ":row:" in unit["unit_id"]]
        narrative_units = [unit for unit in b_units if ":narrative:" in unit["unit_id"]]
        self.assertEqual(len(row_units), 2)
        self.assertGreaterEqual(len(narrative_units), 2)
        row1 = next(unit for unit in row_units if "IS0100101" in unit["unit_id"])
        self.assertIn("기록번호", row1["text"])
        self.assertIn("대상별 처리 원문", row1["text"])
        self.assertIn("설명 셀의 두 번째 줄 40GP", row1["text"])
        self.assertIn("event:GR27F09105", row1["event_ids"])
        self.assertEqual(index["code_to_events"]["IS0100101"], ["event:GR27F09105"])
        self.assertEqual(index["code_to_events"]["GR27F09105"], ["event:GR27F09105"])
        self.assertIn(
            "도입 설명은 원문 상태로 유지합니다.",
            "\n".join(unit["text"] for unit in narrative_units),
        )
        self.assertEqual(stats["families"]["IS"]["row_units"], 2)

    def test_id_free_repeated_reservation_table_splits_rows(self) -> None:
        lines = [
            "IS22 | 예약기록 담당",
            "",
            "예약 관리 자료집",
            "",
            "예약 상태",
            "",
            "예약 상태 / 확인 표",
            "",
            "해당 페이지의 안내 문단입니다.",
            "",
            "예약 상태 — 대상별 원문",
            "",
            "예약·장비          화주·화물          도착·규격          상태",
            "",
            "GR27-F09102         한빛무역            N1 / 40GP           확정",
            "P27-20-09102        금속 브래킷",
            "",
            "GR27-F09103         서림산업            S2 / 20GP           보류",
            "P27-20-09103        절연 커버",
            "",
            "GR27-F09104         동진상사            N1 / 40GP           확정",
            "P27-40-09104        케이블 지지대",
            "",
            "3 / 150",
        ]
        units, index, stats = build_artifacts([make_page("AS028:011", lines)])
        row_units = [
            unit
            for unit in units
            if unit["corpus"] == "B" and ":row:" in unit["unit_id"]
        ]
        self.assertEqual(len(row_units), 3)
        self.assertTrue(any("P27-20-09102" in unit["text"] for unit in row_units))
        self.assertEqual(index["code_to_events"]["GR27F09102"], ["event:GR27F09102"])
        self.assertEqual(stats["families"]["IS"]["structured_pages"], 1)

    def test_fresh_pdf_hash_manifest_matches_sources(self) -> None:
        manifest_path = (
            ROOT
            / "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/raw-manifest.json"
        )
        if not manifest_path.exists():
            self.skipTest("fresh raw-manifest.json is not available in this checkout")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        self.assertEqual(len(files), 120)
        self.assertEqual(len({item["book_id"] for item in files}), 120)
        self.assertEqual(manifest["pages"], 18_000)
        for item in files:
            self.assertEqual(item["pages"], 150)
            pdf_path = ROOT / "data/advanced/documents" / f"{item['book_id']}.pdf"
            self.assertTrue(pdf_path.exists(), str(pdf_path))
            self.assertEqual(
                hashlib.sha256(pdf_path.read_bytes()).hexdigest(), item["pdf_sha256"]
            )

    def test_full_fresh_corpus_line_and_value_coverage(self) -> None:
        pages_path = (
            ROOT / "artifacts/u_10o0o/advanced-fresh-record-hybrid-20261111/pages.jsonl"
        )
        if not pages_path.exists():
            self.skipTest("fresh pages.jsonl is not available in this checkout")
        pages = list(read_pages(pages_path))
        units, relation_index, stats = build_artifacts(pages)
        self.assertEqual(len(pages), 18_000)
        self.assertEqual(stats["pages"], 18_000)
        self.assertEqual(stats["units_by_corpus"]["A"], 18_000)

        pages_by_id = {page["source_id"]: page for page in pages}
        a_units = [unit for unit in units if unit["corpus"] == "A"]
        self.assertEqual(len(a_units), len(pages))
        for unit in a_units:
            page = pages_by_id[unit["sources"][0]]
            self.assertEqual(unit["text"], page["text"])
            self.assertEqual(unit["line_spans"][0]["start"], 1)
            self.assertEqual(unit["line_spans"][0]["end"], len(page["lines"]))

        covered: dict[str, set[int]] = defaultdict(set)
        for unit in (item for item in units if item["corpus"] == "B"):
            page = pages_by_id[unit["sources"][0]]
            if unit["unit_id"].endswith(":raw"):
                self.assertEqual(unit["text"], page["text"])
            for source_id, number, original_line in lines_covered(unit):
                self.assertEqual(source_id, page["source_id"])
                self.assertEqual(original_line, page["lines"][number - 1])
                if original_line.strip():
                    covered[source_id].add(number)
        for source_id, page in pages_by_id.items():
            expected = {i + 1 for i, line in enumerate(page["lines"]) if line.strip()}
            self.assertTrue(expected.issubset(covered[source_id]), source_id)
        b_unit_ids = {unit["unit_id"] for unit in units if unit["corpus"] == "B"}
        self.assertNotIn("20GP", relation_index["code_to_events"])
        self.assertNotIn("40GP", relation_index["code_to_events"])
        self.assertNotIn("00012", relation_index["code_to_events"])
        for event_id, event in relation_index["events"].items():
            self.assertTrue(set(event["unit_ids"]).issubset(b_unit_ids))
            for relation in event["relations"]:
                source_page = pages_by_id[relation["source_id"]]
                source_line = source_page["lines"][relation["start"] - 1]
                self.assertEqual(
                    normalize_code(relation["code"]), relation["normalized_code"]
                )
                self.assertIn(relation["normalized_code"], normalize_code(source_line))
                self.assertIn(
                    event_id,
                    relation_index["code_to_events"][relation["normalized_code"]],
                )
        self.assertGreater(stats["families"]["IS"]["row_units"], 0)
        self.assertEqual(stats["families"]["HG"]["structured_pages"], 13_650)
        self.assertTrue(relation_index["events"])
        for normalized, event_ids in relation_index["code_to_events"].items():
            self.assertTrue(normalized)
            self.assertLessEqual(len(event_ids), 2, normalized)


if __name__ == "__main__":
    unittest.main()


def test_code_at_start_of_prose_is_not_a_table_record():
    from experimental.u_10o0o.fresh_record_hybrid.units import build_artifacts

    lines = [
        "IS01 | 시험 회사",
        "",
        "실제 사건 기록",
        "",
        "현장 기록",
        "",
        "검증 표",
        "",
        "양하 증빙          예약·장비          상태",
        "",
        "DL-A-001          GR27-F001          확인",
        "",
        "DL-A-002          GR27-F002          확인",
        "",
        "DL-A-003          GR27-F003          미결",
        "",
        "DL-A-001은 앞선 기록을 가리키며 실제일과 예정일을 합치지 않는다.",
        "",
        "1 / 1",
    ]
    text = "\n".join(lines)
    units, relations, _ = build_artifacts(
        [
            {
                "source_id": "TEST:001",
                "book_id": "TEST",
                "page": 1,
                "lines": lines,
                "text": text,
            }
        ]
    )
    rows = [u for u in units if u["corpus"] == "B" and ":row:" in u["unit_id"]]
    assert len(rows) == 3
    assert relations["code_to_events"]["DLA001"] == ["event:GR27F001"]
    assert any(
        "DL-A-001은" in u["text"] for u in units if ":narrative:" in u["unit_id"]
    )
