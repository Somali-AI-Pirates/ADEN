"""검증용 가상 사례를 이용한 Judge 동작 검증(대체 근거 인정). 실제 프로젝트 데이터·M19 검증·검색 평가와 무관하다.

모든 문서·예약·장비·계약은 가상이며 ID에 `SYN-`을 붙인다. 질문·답변·참고 정답은 고정하고 규칙 문서(S1)만 바꾼다.
참고 정답의 근거 그룹에는 원계약만 넣어, 대체 문서가 정답 근거 목록에 없어도 인정되는지 본다.
인용 메타데이터(citations)는 답변의 자료 ID를 그 사례 문맥에 대응시킨 것이다(답변 문장은 같다).

    python -m experimental.Damsebi.judge_synthetic --set-id judge-synthetic-20261111
    python -m app.evaluation.scripts.regrade --source judge-synthetic-20261111 \
        --references artifacts/experiments/judge-synthetic-20261111/references.json --judge-model gpt-6-luna
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from app.observability.records import compute_context_hash, normalize_run_record

ROOT = Path(__file__).resolve().parents[2]
QUESTION_ID = "SYN-ALT-01"
QUESTION = ("2031-03-20 16:00 KST 기준 예약 SYN-BK-0001·장비 SYN-40GP-0001 건의 보관료를 실제 사건과 적용 규칙으로 "
            "검산해 주세요. 산입·무료·유료일 수와 금액을 알려 주세요.")
ANSWER = (
    "보관료 검산 결과는 다음과 같습니다.\n"
    "- 적용 규칙: 실제 양하일을 제외하고 다음 날부터 실제 적입 장비 반출일까지 달력일로 산입하며, 기본 무료 3일, "
    "유료 1~3일째 하루 USD 20, 4일째부터 하루 USD 50입니다. [S1]\n"
    "- 실제 사건: 실제 양하일 2031-03-02, 실제 적입 장비 반출일 2031-03-10입니다. [S2]\n"
    "- 산입 기간: 2031-03-03~2031-03-10, 8일입니다. 무료 3일(03-03~03-05), 유료 5일(03-06~03-10)입니다. [S1][S2]\n"
    "- 계산: 3일×USD 20 + 2일×USD 50 = USD 160입니다. [S1]\n"
    "- 청구서상 보관료 USD 160과 직접 계산한 금액이 일치합니다. [S3]")
REASON = "적용 규칙, 실제 사건 날짜, 청구 명세를 연결해 보관료를 직접 계산했고 청구액과 비교했습니다. [S1][S2][S3]"
SOURCE_IDS = ["S1", "S2", "S3"]

TARGET = "예약 SYN-BK-0001 · 장비 SYN-40GP-0001(40GP) · 계약 SYN-CT-0001 · 화주 가상상사 · 가상 부품 10상자"
DOCS = {
    "contract": {
        "book_id": "SYN-CONTRACT", "file_name": "SYN-CONTRACT.pdf", "page": 1,
        "heading": f"SYN-CT-0001 비용 조건 약정 > 계약일 2031-02-01 / {TARGET}",
        "text": ("계약 SYN-CT-0001의 보관료 조건이다. 보관료는 실제 양하일을 제외하고 다음 날부터 실제 적입 장비 반출일까지 "
                 "포함하며 달력일로 센다. 기본 무료는 3일이다. 유료일 순번 1~3일은 하루 USD 20, 4일부터는 하루 USD 50이다. "
                 "유료일 순번은 무료기간이 끝난 뒤 1부터 시작한다. 이 약정은 2031-02-01부터 2031-12-31까지 반출되는 "
                 "이 계약의 장비에 적용한다. 같은 화주의 다른 예약이나 규격은 이 계약의 대상이 아니다."),
        "review": {"target": "SYN-CT-0001 / SYN-BK-0001 / SYN-40GP-0001", "valid": "2031-02-01~2031-12-31 반출분",
                   "rule": {"start": "양하일 다음 날", "end": "반출일 포함", "basis": "달력일", "free": 3,
                            "tiers": [[3, 20], [None, 50]]}, "applicable": True},
    },
    "alternate": {
        "book_id": "SYN-CONFIRM", "file_name": "SYN-CONFIRM.pdf", "page": 4,
        "heading": "계약 조건 확인서 > 계약운영실 발행 2031-02-05 / 대상 계약 SYN-CT-0001 · 예약 SYN-BK-0001 · 장비 SYN-40GP-0001",
        "text": ("계약운영실이 계약 SYN-CT-0001에 속한 예약 SYN-BK-0001, 장비 SYN-40GP-0001의 보관료 산정 방법을 "
                 "확인한다. 양하가 이루어진 날은 보관료 계산에 넣지 않는다. 양하 다음 날을 첫날로 삼아 장비를 실제로 "
                 "적입해 내보낸 날까지, 주말과 휴일을 가리지 않고 하루씩 센다. 그중 처음 사흘은 요금을 받지 않는다. "
                 "요금이 붙는 날은 첫째 날부터 셋째 날까지 하루 미화 20달러, 넷째 날부터는 하루 미화 50달러다. "
                 "이 확인서는 2031-02-01 이후 2031-12-31까지 반출되는 위 장비에 유효하다."),
        "review": {"target": "SYN-CT-0001 / SYN-BK-0001 / SYN-40GP-0001", "valid": "2031-02-01~2031-12-31 반출분",
                   "rule": {"start": "양하일 다음 날", "end": "반출일 포함", "basis": "달력일", "free": 3,
                            "tiers": [[3, 20], [None, 50]]}, "applicable": True},
    },
    "other_target": {
        "book_id": "SYN-CONTRACT-B", "file_name": "SYN-CONTRACT-B.pdf", "page": 1,
        "heading": ("SYN-CT-0002 비용 조건 약정 > 계약일 2031-02-01 / 예약 SYN-BK-0002 · 장비 SYN-20GP-0002(20GP) · "
                    "계약 SYN-CT-0002 · 화주 가상상사 · 가상 부품 4상자"),
        "text": ("계약 SYN-CT-0002의 보관료 조건이다. 보관료는 실제 양하일을 제외하고 다음 날부터 실제 적입 장비 반출일까지 "
                 "포함하며 달력일로 센다. 기본 무료는 3일이다. 유료일 순번 1~3일은 하루 USD 20, 4일부터는 하루 USD 50이다. "
                 "유료일 순번은 무료기간이 끝난 뒤 1부터 시작한다. 이 약정은 2031-02-01부터 2031-12-31까지 반출되는 "
                 "이 계약의 장비에 적용한다. 같은 화주의 다른 예약이나 규격은 이 계약의 대상이 아니다."),
        "review": {"target": "SYN-CT-0002 / SYN-BK-0002 / SYN-20GP-0002 (질문 대상 아님)",
                   "valid": "2031-02-01~2031-12-31 반출분",
                   "rule": {"start": "양하일 다음 날", "end": "반출일 포함", "basis": "달력일", "free": 3,
                            "tiers": [[3, 20], [None, 50]]}, "applicable": False},
    },
    "expired": {
        "book_id": "SYN-TARIFF-2030", "file_name": "SYN-TARIFF-2030.pdf", "page": 1,
        "heading": "보관료 요금 고시 2030년판 > 적용 대상 가상해운 일반 계약(SYN-CT 계열) / 적용 기간 2030년 반출분",
        "text": ("가상해운 일반 계약(SYN-CT 계열 포함)의 보관료 고시다. 2030-01-01부터 2030-12-31까지 반출된 장비에 "
                 "적용한다. 보관료는 실제 양하일 다음 날부터 실제 반출일까지 달력일 기준으로 산입한다. 무료 3일, 유료 "
                 "1~3일 USD 20/일, 4일부터 USD 50/일이다. 2031-01-01 이후 반출분에는 2031년 계약 조건을 따른다."),
        "review": {"target": "SYN-CT 계열 일반 계약", "valid": "2030-01-01~2030-12-31 반출분 (2031-03-10 반출은 기간 밖)",
                   "rule": {"start": "양하일 다음 날", "end": "반출일 포함", "basis": "달력일", "free": 3,
                            "tiers": [[3, 20], [None, 50]]}, "applicable": False},
    },
    "unrelated": {
        "book_id": "SYN-RETURN", "file_name": "SYN-RETURN.pdf", "page": 5,
        "heading": f"장비 반납 안내 > {TARGET}",
        "text": ("예약 SYN-BK-0001의 공장비는 지정 장치장 B구역에 반납한다. 반납 때 인수증을 받아 보관한다. "
                 "반납 예약은 전날 17시까지 신청한다. 반납 예약일은 예정 정보이며 실제 반납일이 아니다."),
        "review": {"target": "SYN-BK-0001", "valid": "-", "rule": None, "applicable": False},
    },
}
EVENTS = {"book_id": "SYN-EVENT", "file_name": "SYN-EVENT.pdf", "page": 2,
          "heading": f"현장 사건 기록 > 기록 기준 2031-03-20 16:00 KST / {TARGET}",
          "text": ("실제 양하일: 2031-03-02(확인). 실제 적입 장비 반출일: 2031-03-10(확인). 반출 예약 예정일 2031-03-08은 "
                   "예정 정보이며 실제 일자가 아니다. 현장 확인은 장비번호와 사건 유형을 기준으로 묶었다.")}
BILL = {"book_id": "SYN-BILL", "file_name": "SYN-BILL.pdf", "page": 3,
        "heading": f"비용 청구 명세 > 조회 기준 2031-03-20 16:00 KST / {TARGET}",
        "text": ("보관료: 산입 8일 / 무료 3일 / 유료 5일, 3일×20 + 2일×50 = 160. 확정 청구액 USD 160. "
                 "청구 명세는 정산실이 작성한 청구 기록이다.")}
CASES = [
    ("syn-1-original", "contract", "원계약에 필요한 규칙이 있는 기준 사례"),
    ("syn-2-alternate", "alternate", "원계약 없이 같은 대상·기간에 유효하고 같은 의미의 규칙을 다른 표현으로 담은 확인서만 제공"),
    ("syn-3a-other-target", "other_target", "규칙은 같지만 다른 예약·장비·계약에 적용되는 약정만 제공"),
    ("syn-3b-expired", "expired", "규칙은 같지만 적용 기간(2030년 반출분)이 지난 고시만 제공"),
    ("syn-4-no-rule", "unrelated", "보관료 규칙이 어느 문서에도 없음(반납 안내만 제공)"),
]


def storage_fee(rule: dict, discharge: date, gate_out: date) -> tuple[int, int, int, int]:
    """검수용: 문서 규칙 파라미터로 계산한 (산입, 무료, 유료, 금액)."""
    days = (gate_out - (discharge + timedelta(days=1))).days + 1
    free = min(days, rule["free"])
    paid = days - free
    amount, counted = 0, 0
    for limit, rate in rule["tiers"]:
        take = paid - counted if limit is None else min(limit, paid - counted)
        amount += take * rate
        counted += take
    return days, free, paid, amount


def build(set_id: str) -> tuple[list[dict], dict]:
    records = []
    for case, doc_key, description in CASES:
        chunks = []
        for number, doc in enumerate((DOCS[doc_key], EVENTS, BILL), start=1):
            chunks.append({"chunk_id": f"{doc['book_id']}-p{doc['page']}-c1", "book_id": doc["book_id"],
                           "file_name": doc["file_name"], "page": doc["page"], "text": doc["text"],
                           "heading": doc["heading"], "source_id": f"S{number}"})
        citations = []
        for chunk in chunks:
            if chunk["source_id"] in SOURCE_IDS:
                citations.append({"source_id": chunk["source_id"], "book_id": chunk["book_id"],
                                  "file_name": chunk["file_name"], "page": chunk["page"]})
        record = {
            "experiment_id": set_id, "question_id": QUESTION_ID, "request_id": f"{set_id}-{case}",
            "dataset_id": "synthetic-judge-check", "retrieval_mode": "hybrid_rerank", "config_hash": "synthetic",
            "question": QUESTION, "retrieved_candidates": chunks, "final_context_chunks": chunks,
            "final_context_hash": compute_context_hash(chunks), "status": "answered", "answer": ANSWER,
            "reason": REASON, "citations": citations, "latency_ms": None, "attempts": [],
            "langfuse_trace_id": None,
            "judge_case": {"case": case, "description": description, "rule_document": doc_key,
                           "review": DOCS[doc_key]["review"], "synthetic": True},
        }
        records.append(normalize_run_record(record))
    rule_quote = DOCS["contract"]["text"].split(" 이 약정은")[0]
    reference = {
        "id": QUESTION_ID, "question": QUESTION,
        "reference_answer": ("보관료는 산입 8일(2031-03-03~03-10), 무료 3일, 유료 5일이며 3일×USD 20 + 2일×USD 50 = "
                             "USD 160으로 청구액 USD 160과 일치한다."),
        "required_facts": ["산입 8일", "무료 3일·유료 5일", "USD 160", "청구액과 일치"],
        "expected_action": "answer", "accepted_action_labels": ["answer"],
        "expected_behavior": "질문 범위의 사실·조건·계산·판단을 근거와 함께 답변",
        "evidence_groups": [[{"book_id": "SYN-CONTRACT", "page": 1, "quote": rule_quote}],
                            [{"book_id": "SYN-EVENT", "page": 2, "quote": EVENTS["text"].split(" 반출 예약")[0]}]],
        "forbidden_claims": ["다른 예약·기간의 규칙을 적용했다고 하지 않는다"],
        "grading_notes": ("질문이 요청한 사실만 필수이다. 표·문장·동등한 수치 표현과 충분한 대체 원문 근거를 인정한다. "
                          "근거 목록 전부의 인용 개수를 요구하지 않는다. 의미상 부분 보류와 action 라벨 일치를 별도로 평가한다."),
    }
    return records, reference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--set-id", required=True)
    parser.add_argument("--artifacts-dir", default=str(ROOT / "artifacts" / "experiments"))
    args = parser.parse_args()
    # 검수: 적용 가능한 문서의 규칙으로 계산하면 답변의 숫자(8/3/5, USD 160)와 같아야 한다
    for key, doc in DOCS.items():
        rule = doc["review"]["rule"]
        if rule:
            assert storage_fee(rule, date(2031, 3, 2), date(2031, 3, 10)) == (8, 3, 5, 160), key
    out = Path(args.artifacts_dir) / args.set_id
    if out.exists():
        raise SystemExit(f"{out}가 이미 있다")
    records, reference = build(args.set_id)
    out.mkdir(parents=True)
    with (out / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    (out / "references.json").write_text(json.dumps([reference], ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"experiment_id": args.set_id, "dataset_id": "synthetic-judge-check", "config_hash": "synthetic",
                "sample": False, "kind": "synthetic-judge-case-set",
                "label": "검증용 가상 사례를 이용한 Judge 동작 검증(실제 데이터·검색 평가 아님)",
                "benchmark": {"k": 8, "modes": ["hybrid_rerank"], "question_ids": [QUESTION_ID],
                              "selection_reason": "대체 근거 인정 동작 검증(가상 사례)", "judge": None,
                              "faithfulness": None, "judge_prompt": None},
                "cases": [record["judge_case"] | {"request_id": record["request_id"]} for record in records]}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for record in records:
        print(record["judge_case"]["case"], record["final_context_chunks"][0]["file_name"],
              "적용 가능" if record["judge_case"]["review"]["applicable"] else "적용 불가/규칙 없음")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
