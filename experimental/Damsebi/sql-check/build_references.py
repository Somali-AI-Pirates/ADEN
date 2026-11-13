"""SQL 검증 개발 문항(SQL-DEV) 초안의 질문·참고 파일을 만든다(모델 호출 없음).

기대값은 손으로 적은 값과, 에이전트와 같은 SQL 엔진(TableStore.query)으로 기준 SQL을 실행한 결과를 대조해 만든다.
둘이 다르면 파일을 쓰지 않고 멈춘다. additional·development 평가 세트의 근거 페이지는 쓰지 않는다.

    python -m experimental.Damsebi.sql-check.build_references  (패키지 이름에 '-'가 있어 직접 실행한다)
    python experimental/Damsebi/sql-check/build_references.py --bundle artifacts/advanced-table-rag
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.rag.table_store import TableStore  # noqa: E402

HERE = Path(__file__).resolve().parent

# 문항: 질문, 기준 SQL(표 ID, SQL, 확인할 열·값), 답변에 있어야 할 값, 답변에 있으면 안 되는 표현
QUESTIONS = [
    {
        "id": "SQL-DEV01",
        "type": "상태 구분 집계(표 1개)",
        "question": "청람터미널 HA28-C013 비용별 정산 확인에서 금액이 확정된 정산 항목과 미확정인 정산 항목은 각각 "
                    "몇 개인가요? 확정된 보관료 정산액도 알려 주세요.",
        "queries": [
            {"tables": ["t_677a070c4167b4b7"],
             "sql": "SELECT c0_state AS state, COUNT(*) AS n FROM t_677a070c4167b4b7 GROUP BY c0_state ORDER BY state",
             "expect_rows": [{"state": "reported", "n": 3}, {"state": "미확정", "n": 3}]},
            {"tables": ["t_677a070c4167b4b7"],
             "sql": "SELECT c0_value AS amount, c0_unit AS unit FROM t_677a070c4167b4b7 WHERE c2 = '보관료 정산액'",
             "expect_rows": [{"amount": "165", "unit": "USD"}]},
        ],
        "expected_values": [{"label": "확정 항목 수", "value": "3"}, {"label": "미확정 항목 수", "value": "3"},
                            {"label": "보관료 정산액", "value": "165", "unit": "USD"}],
        "answer_must_not": [],
    },
    {
        "id": "SQL-DEV02",
        "type": "미결·미확정 구분(표 1개)",
        "question": "HG2029-00074 가람자재 HL-40GP-00074 건의 수납·미결 인계에서 금액이 확정된 항목과 그 금액을 "
                    "알려 주세요. 금액이 아직 미확정인 항목은 몇 개인가요?",
        "queries": [
            {"tables": ["t_73b5dc3ba9d5eb86"],
             "sql": "SELECT c0 AS item, c1_value AS amount, c1_unit AS unit FROM t_73b5dc3ba9d5eb86 "
                    "WHERE c1_state = 'reported' ORDER BY item",
             "expect_rows": [{"item": "보관료", "amount": "335", "unit": "USD"},
                             {"item": "수납", "amount": "0", "unit": "USD"}]},
            {"tables": ["t_73b5dc3ba9d5eb86"],
             "sql": "SELECT COUNT(*) AS n FROM t_73b5dc3ba9d5eb86 WHERE c1_state = '미확정'",
             "expect_rows": [{"n": 3}]},
        ],
        "expected_values": [{"label": "보관료", "value": "335", "unit": "USD"},
                            {"label": "수납", "value": "0", "unit": "USD"},
                            {"label": "미확정 항목 수", "value": "3"}],
        "answer_must_not": [],
    },
    {
        "id": "SQL-DEV03",
        "type": "미결을 0으로 보지 않기(표 1개, 함정)",
        "question": "HG2030-01246 태산부품 HL-20GP-01246 건의 거래 합계는 지금 얼마로 확정할 수 있나요?",
        "queries": [
            {"tables": ["t_b0576c96f32a9a73"],
             "sql": "SELECT c0 AS item, c1_state AS state, c1_value AS amount FROM t_b0576c96f32a9a73 "
                    "WHERE c0 IN ('보관료', '장비반납지연료', '거래 합계') ORDER BY item",
             "expect_rows": [{"item": "거래 합계", "state": "미확정", "amount": None},
                             {"item": "보관료", "state": "reported", "amount": "480"},
                             {"item": "장비반납지연료", "state": "미확정", "amount": None}]},
        ],
        "expected_values": [{"label": "보관료 확정액", "value": "480", "unit": "USD"},
                            {"label": "거래 합계 상태", "value": "미확정"}],
        "answer_must_not": ["거래 합계(는|가|를)? *(USD *)?480", "합계(는|가)? *USD *480"],
    },
    {
        "id": "SQL-DEV04",
        "type": "두 표 건수 합산(같은 자료집)",
        "question": "가온루트해운 IS01 자료집에서 접수번호 IS01-002와 IS01-003 대상 중 안내 범위에 해당하는 건은 "
                    "모두 몇 건이며, 접수번호는 무엇인가요?",
        "queries": [
            {"tables": ["t_44b044213394fb58", "t_a241091593ab66e7"],
             "sql": "SELECT c4 AS receipt FROM t_44b044213394fb58 WHERE c3 = '안내 범위' "
                    "UNION ALL SELECT c4 FROM t_a241091593ab66e7 WHERE c3 = '안내 범위' ORDER BY receipt",
             "expect_rows": [{"receipt": "IS01-002-07"}, {"receipt": "IS01-002-08"}, {"receipt": "IS01-003-06"},
                             {"receipt": "IS01-003-07"}]},
        ],
        "expected_values": [{"label": "안내 범위 건수", "value": "4"}, {"label": "접수번호", "value": "IS01-002-07"},
                            {"label": "접수번호", "value": "IS01-002-08"},
                            {"label": "접수번호", "value": "IS01-003-06"},
                            {"label": "접수번호", "value": "IS01-003-07"}],
        "answer_must_not": [],
    },
    {
        "id": "SQL-DEV05",
        "type": "조건별 건수(표 1개)",
        "question": "IS01 자료집의 접수번호 IS01-002 대상 중 안내 범위에 해당하지 않는 건은 몇 건인가요? 그중 항로가 "
                    "부산–LA인데도 안내 범위가 아닌 건은 몇 건인가요?",
        "queries": [
            {"tables": ["t_44b044213394fb58"],
             "sql": "SELECT SUM(c3 <> '안내 범위') AS outside, "
                    "SUM(c3 <> '안내 범위' AND c2 = '부산–LA') AS outside_busan_la "
                    "FROM t_44b044213394fb58",
             "expect_rows": [{"outside": 6, "outside_busan_la": 2}]},
        ],
        "expected_values": [{"label": "안내 범위 밖", "value": "6"},
                            {"label": "부산–LA인데 안내 범위 밖", "value": "2"}],
        "answer_must_not": [],
    },
    {
        "id": "SQL-DEV06",
        "type": "문자열·날짜 조건 건수(표 1개)",
        "question": "IS25 N1 1월 운영 기록의 계획번호 IS25-049 항목 중 40GP 대상은 몇 건인가요? 반출예정일이 "
                    "2027-01-22 이후(당일 포함)인 항목은 몇 건인가요?",
        "queries": [
            {"tables": ["t_1b8479a3374610db"],
             "sql": "SELECT SUM(c1 LIKE '%N1-40-%') AS gp40, "
                    "SUM(substr(c2, length(c2) - 9, 10) >= '2027-01-22') AS gate_out_from_0122 FROM t_1b8479a3374610db",
             "expect_rows": [{"gp40": 3, "gate_out_from_0122": 4}]},
        ],
        "expected_values": [{"label": "40GP 건수", "value": "3"}, {"label": "반출예정 2027-01-22 이후", "value": "4"}],
        "answer_must_not": [],
        "notes": "모든 항목이 예정 기록이며 실제 확인은 별도라는 점을 답변이 구분하는지 함께 본다.",
    },
    {
        "id": "SQL-DEV07",
        "type": "단위 구분·변경 비교(표 1개)",
        "question": "IS17 2027년 2월 요율 기록의 대조번호 IS17-001 항목 중 기존값과 확인값이 달라진 항목은 무엇이고 "
                    "얼마나 바뀌었나요? 일요율 항목과 무료일수 항목은 각각 몇 개인가요?",
        "queries": [
            {"tables": ["t_ab8ed58e95a4bf0d"],
             "sql": "SELECT c1 AS field, c0_value AS before, c4_value AS after, "
                    "decimal_sub(c4_value, c0_value) AS diff, "
                    "c4_unit AS unit FROM t_ab8ed58e95a4bf0d WHERE decimal_cmp(c0_value, c4_value) <> 0",
             "expect_rows": [{"field": "N1 40GP 일요율", "before": "68", "after": "74", "diff": "6", "unit": "USD"}]},
            {"tables": ["t_ab8ed58e95a4bf0d"],
             "sql": "SELECT SUM(c1 LIKE '%일요율') AS daily_rate, SUM(c1 LIKE '%무료일수') AS free_days "
                    "FROM t_ab8ed58e95a4bf0d",
             "expect_rows": [{"daily_rate": 4, "free_days": 4}]},
        ],
        "expected_values": [{"label": "변경 전", "value": "68", "unit": "USD"},
                            {"label": "변경 후", "value": "74", "unit": "USD"},
                            {"label": "차이", "value": "6", "unit": "USD"}, {"label": "일요율 항목 수", "value": "4"},
                            {"label": "무료일수 항목 수", "value": "4"}],
        "answer_must_not": [],
    },
    {
        "id": "SQL-DEV08",
        "type": "두 사건 합계(표 2개)",
        "question": "청람터미널 HA28-C004와 HA28-C013의 확정된 보관료 정산액을 합하면 얼마인가요?",
        "queries": [
            {"tables": ["t_e38a5742a136596a", "t_677a070c4167b4b7"],
             "sql": "SELECT decimal_sum(amount) AS total FROM (SELECT c0_value AS amount FROM t_e38a5742a136596a "
                    "WHERE c2 = '보관료 정산액' AND c0_state = 'reported' "
                    "UNION ALL SELECT c0_value FROM t_677a070c4167b4b7 "
                    "WHERE c2 = '보관료 정산액' AND c0_state = 'reported')",
             "expect_rows": [{"total": "475"}]},
        ],
        "expected_values": [{"label": "HA28-C004 보관료 정산액", "value": "310", "unit": "USD"},
                            {"label": "HA28-C013 보관료 정산액", "value": "165", "unit": "USD"},
                            {"label": "합계", "value": "475", "unit": "USD"}],
        "answer_must_not": [],
    },
]


def _same(actual, expected) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        try:
            return Decimal(actual) == Decimal(expected)
        except ArithmeticError:
            return actual == expected
    return actual == expected


def check_rows(rows: list[dict], expected: list[dict]) -> bool:
    if len(rows) != len(expected):
        return False
    for row, want in zip(rows, expected, strict=True):
        for key, value in want.items():
            if key not in row or not _same(row[key], value):
                return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--bundle", default=str(ROOT / "artifacts" / "advanced-table-rag"))
    parser.add_argument("--out", default=str(HERE))
    args = parser.parse_args()

    store = TableStore(args.bundle)
    used_pages = set()
    for name in ("additional.json", "development.json"):
        path = ROOT / "data" / "advanced" / "evaluation" / name
        for reference in json.loads(path.read_text(encoding="utf-8")):
            for group in reference.get("evidence_groups") or []:
                for item in group:
                    used_pages.add((item["book_id"], item["page"]))

    questions = []
    references = []
    for item in QUESTIONS:
        sql_results = []
        pages = []
        for query in item["queries"]:
            for table_id in query["tables"]:
                table = store.catalog[table_id]
                page = (table["book_id"], table["page"])
                if page in used_pages:
                    raise SystemExit(f"{item['id']}: 평가 세트 근거 페이지를 쓴다 {page}")
                if [{"book_id": page[0], "page": page[1]}] not in pages:
                    pages.append([{"book_id": page[0], "page": page[1]}])
            result = store.query(query["sql"], query["tables"])
            if result["status"] != "ok" or not check_rows(result["rows"], query["expect_rows"]):
                raise SystemExit(f"{item['id']}: 기준 SQL 결과가 예상과 다르다\n{query['sql']}\n{result}")
            sql_results.append({"columns": result["columns"], "rows": result["rows"]})
        questions.append({"id": item["id"], "question": item["question"]})
        references.append({"id": item["id"], "question": item["question"], "type": item["type"],
                           "expected_action": "answer", "evidence_groups": pages,
                           "expected_values": item["expected_values"], "answer_must_not": item["answer_must_not"],
                           "reference_sql": [{"tables": q["tables"], "sql": q["sql"]} for q in item["queries"]],
                           "expected_sql_results": sql_results, "notes": item.get("notes", ""),
                           "status": "draft: 원문 페이지와 사람 대조 전"})
    out = Path(args.out)
    (out / "sql-dev-questions.json").write_bytes(
        (json.dumps(questions, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    (out / "sql-dev-references.json").write_bytes(
        (json.dumps(references, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"{len(questions)}문항: 기준 SQL {sum(len(q['queries']) for q in QUESTIONS)}개 모두 예상 결과와 일치 → {out}")


if __name__ == "__main__":
    main()
