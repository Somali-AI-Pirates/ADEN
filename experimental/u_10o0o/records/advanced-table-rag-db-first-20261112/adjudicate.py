"""Apply inspected evidence decisions; preserve raw model drafts and exact-source reuse."""

from copy import deepcopy
from collections import Counter
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import ROOT, read_json, write_json

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v1"
OLD = ROOT / "artifacts/u_10o0o/advanced-table-rag-unified-20261112/v3"


def main():
    if (OUT / "adjudications-model.json").exists():
        raise FileExistsError("Preserve existing completed adjudications")
    rows = read_json(OUT / "adjudications.json")
    write_json(OUT / "adjudications-model.json", rows)
    old, new = read_json(OLD / "protocol.json"), read_json(OUT / "protocol.json")
    for key in ("questions_sha256", "references_sha256", "question_metadata_sha256"):
        assert old[key] == new[key]
    previous = read_json(OLD / "adjudications.json")
    inspected = set(read_json(OUT / "assistant-inspected.json"))
    changes, reused = [], []
    for row in rows:
        matches = [
            r
            for r in previous
            if r["id"] == row["id"] and r["source_hashes"] == row["source_hashes"]
        ]
        if matches:
            assert len({r["verdict"] for r in matches}) == 1
            prior = matches[0]
            assert prior["assistant_checked"]
            for key in ("verdict", "claims", "scope_ok", "reason", "public_reason"):
                if key in prior:
                    row[key] = deepcopy(prior[key])
            row["reused_from"] = {
                "run": OLD.name,
                "experiment": OLD.parent.name,
                "arm": prior["arm"],
            }
            reused.append(
                {"id": row["id"], "arm": row["arm"], "prior_arm": prior["arm"]}
            )
        else:
            assert row["id"] + "|" + row["arm"] in inspected
        row["assistant_checked"] = True
    indexed = {(r["id"], r["arm"]): r for r in rows}
    inputs = {(r["id"], r["arm"]): r for r in read_json(OUT / "review-inputs.json")}

    def get(rid, arm):
        return indexed[(rid, arm)]

    def text(rid, arm, source):
        return next(
            p["text"]
            for p in inputs[(rid, arm)]["context"]
            if f"{p['book_id']}:p{p['page']:04d}" == source
        )

    def paragraph(rid, arm, source, contains):
        return next(p for p in text(rid, arm, source).split("\n\n") if contains in p)

    def proof(source, quote):
        return {"source_id": source, "quote": quote}

    def record(rid, arm, reason):
        changes.append({"id": rid, "arm": arm, "change": reason})

    rid, arm = "official_additional:AS-ADD06", "A"
    claim = get(rid, arm)["claims"][1]
    claim["evidence"] = [
        e for e in claim["evidence"] if e["source_id"] != "AS001:p0001"
    ]
    assert claim["evidence"]
    record(
        rid,
        arm,
        "잘못된 출처를 가리킨 인용은 제거하고 실제 반환된 SI 정정 연락처 표 행을 유지했다.",
    )
    rid, arm = "official_additional:AS-ADD06", "B"
    row = get(rid, arm)
    row["claims"][1].update(
        status="supported",
        evidence=[
            proof("AS007:p0019", paragraph(rid, arm, "AS007:p0019", "IS01-019-01"))
        ],
        reason="연락처 열에 장비 코드가 끼어든 표 행이다. 실제 행 자체를 인용하고 명시된 역할과 주소를 대조했다.",
    )
    row.update(
        verdict="sufficient",
        scope_ok=True,
        reason="VGM 신규 창구, SI 정정 창구, 중량 구분을 반환 원문에서 확인했다.",
    )
    record(
        rid,
        arm,
        "열이 섞인 SI 정정 연락처를 실제 원문 행에서 확인했다. 이어 붙인 주소를 새 인용으로 만들지 않았다.",
    )

    rid, arm = "official_additional:AS-ADD10", "A"
    row = get(rid, arm)
    row["claims"] = [row["claims"][2]]
    row.update(
        verdict="sufficient",
        scope_ok=True,
        reason="질문은 포털에 신규 등록해야 하는지를 묻는다. 반환된 기존 접수분 중복 제출 제외 규칙으로 판단 가능하며, 추가 정정 절차 전체를 요구하지 않았다.",
    )
    record(rid, arm, "질문 밖 상세 정정 절차를 필수 요구로 확대한 판정을 바로잡았다.")

    rid, arm = "official_development:AS-DEV08", "A"
    get(rid, arm)["claims"][0]["evidence"] = [
        proof(
            "AS013:p0007",
            paragraph(rid, arm, "AS013:p0007", "실제 진행 회신 대기 건은"),
        )
    ]
    record(rid, arm, "문장 끝을 바꾸어 인용한 부분을 실제 반환 원문으로 교체했다.")
    rid, arm = "official_development:AS-DEV26", "B"
    for e in get(rid, arm)["claims"][0]["evidence"]:
        if e["quote"] == "정액 감액 20 0":
            raw = text(rid, arm, "AS078:p0114")
            start = raw.index("항목 보관료 반납지연료")
            e["quote"] = raw[start : raw.index("전액 면제", start)].strip()
    record(
        rid,
        arm,
        "짧은 표 셀 인용에 실제 열 머리말과 앞 행을 포함해 방향과 적용 범위를 보존했다.",
    )
    rid, arm = "semantic_queries:AS-SEM-26", "B"
    get(rid, arm)["claims"][0]["evidence"] = [
        proof("AS012:p0033", paragraph(rid, arm, "AS012:p0033", "화주는 검토를 마친")),
        proof("AS012:p0033", paragraph(rid, arm, "AS012:p0033", "성원물산 건은")),
    ]
    record(
        rid,
        arm,
        "다른 쪽 문구처럼 인용한 부분을 반환 원문의 승인 규칙과 수신만 확인된 기록으로 교체했다.",
    )

    for arm in ("A", "B"):
        rid = "semantic_queries:AS-SEM-24"
        row = get(rid, arm)
        row["claims"][1].update(
            status="supported",
            evidence=[
                proof("AS014:p0019", paragraph(rid, arm, "AS014:p0019", "20GP VGM은"))
            ],
            reason="신규 접수 경로 변경과 기존 접수분의 정정을 구분하는 질문이며, 기존 기록의 포털 중복 제출 제외 규칙이 직접 대응한다.",
        )
        row.update(
            verdict="sufficient",
            scope_ok=True,
            reason="서류 중량 정정 정보와 기존 VGM의 신규 포털 중복 제출 여부를 모두 지원한다. 특정 건의 정정 완료나 특정 금액을 추정하지 않는다.",
        )
        record(
            rid,
            arm,
            "새 접수 화면 사용 여부를 묻는 질문에 대응하는 기존 기록 제외 규칙을 인정했다.",
        )
        rid = "semantic_queries:AS-SEM-32"
        row = get(rid, arm)
        row.update(
            verdict="ambiguous_question",
            scope_ok=False,
            reason="양하일 산입 규칙을 정할 터미널·계약 범위가 질문에 특정되지 않는다. 서로 다른 규칙 중 하나를 임의 선택하지 않고 이전 판정처럼 분모에 남긴다.",
        )
        row["claims"][0].update(
            status="ambiguous",
            reason="대상 계약과 날짜 산입 범위를 특정할 추가 정보가 필요하다.",
        )
        record(
            rid,
            arm,
            "기존에 확인한 날짜 산입 범위 모호성을 재확인하고 성공으로 세지 않았다.",
        )

    rid, arm = "semantic_queries:AS-SEM-36", "A"
    row = get(rid, arm)
    row["claims"][1].update(
        status="supported",
        evidence=[
            proof(
                "AS022:p0007",
                paragraph(rid, arm, "AS022:p0007", "추가 무료기간은 승인서가"),
            ),
            proof(
                "AS017:p0039",
                paragraph(rid, arm, "AS017:p0039", "기본 무료기간과 추가"),
            ),
        ],
        reason="승인된 장비·일수만 무료기간에 적용하며 금액 감액은 유료일수 계산 뒤 별도로 적용한다. 질문에서 돈만 승인된 다른 장비에 일수 승인을 옮길 근거가 없다.",
    )
    row.update(
        verdict="sufficient",
        scope_ok=True,
        reason="장비별 추가 일수 승인과 금액 감액의 별도 적용을 반환 원문으로 구분할 수 있다.",
    )
    record(
        rid, arm, "장비·일수 승인 한정 규칙과 금액 감액의 별도 계산을 함께 인정했다."
    )

    rid, arm = "semantic_queries:AS-SEM-37", "B"
    row = get(rid, arm)
    row["claims"][1].update(
        status="supported",
        evidence=[
            proof("AS051:p0019", paragraph(rid, arm, "AS051:p0019", "발행 통지 SET")),
            proof(
                "AS051:p0019",
                paragraph(rid, arm, "AS051:p0019", "최신 계산 초안은 미발행"),
            ),
        ],
        reason="미발행 검토본은 발행 통지를 대체하지 않는다는 명시 설명이 있다. 질문은 어느 상태의 금액을 안내할지 묻고 특정 사건의 금액을 요구하지 않는다.",
    )
    row.update(
        verdict="sufficient",
        scope_ok=True,
        reason="승인 중복 적용 금지와 미발행 초안·유효 발행 통지의 상태 구분을 지원한다. 예시 사건의 금액을 사용자 사건에 전용하지 않는다.",
    )
    record(
        rid,
        arm,
        "반환된 원문에 명시된 미발행 초안과 발행 통지의 관계를 인정했다. 특정 사건의 금액은 전용하지 않았다.",
    )

    missing = {
        "official_development:AS-DEV06": "SI 최초 제출 창구의 직접 근거가 부족하다.",
        "official_development:AS-DEV09": "SI 정정 메일의 예약 식별정보·새 값·첨부자료 등 제출 내용이 부족하다.",
        "official_additional:AS-ADD09": "봉인 변경을 운송사에 알리는 절차와 기존·새 번호를 함께 보내는 근거가 부족하다.",
        "scenarios:AS-S06-20": "두 후보 중 한 사건의 실제 반출일 근거가 최종 문맥에 없어 질문 단서와의 일치를 확인할 수 없다.",
        "semantic_queries:AS-SEM-18": "보관료 감액을 다른 비용에 확대하지 않는 적용 범위 근거가 부족하다.",
        "semantic_queries:AS-SEM-23": "신규 VGM 제출 경로를 확인할 규정이 부족하다.",
        "semantic_queries:AS-SEM-28": "자료 등록일과 실제 업무값 효력을 구분하는 근거가 부족하다.",
        "semantic_queries:AS-SEM-30": "월말 인계에서 구분할 단계별 상태를 모두 확인할 근거가 부족하다.",
        "semantic_queries:AS-SEM-31": "이미 이메일로 접수한 VGM의 정정 경로가 부족하다.",
        "semantic_queries:AS-SEM-37": "미발행 계산 초안과 유효 발행 통지의 관계를 확인할 근거가 부족하다.",
        "work_rules:AS-WR-20": "내부 청구 준비 메모의 효력과 공식 기록의 관계를 확인할 근거가 부족하다.",
    }
    for row in rows:
        row["public_reason"] = (
            "반환 원문이 질문의 필수 요구와 대상·적용 범위를 뒷받침한다."
            if row["verdict"] == "sufficient"
            else "날짜 산입을 정할 터미널·계약 범위가 모호하여 분모에 남기고 성공으로 세지 않았다."
            if row["verdict"] == "ambiguous_question"
            else missing.get(
                row["id"],
                row.get(
                    "public_reason",
                    "질문의 필수 근거 일부가 최종 문맥에서 확인되지 않는다.",
                ),
            )
        )
    write_json(OUT / "adjudications.json", rows)
    write_json(HERE / "assistant-review-changes.json", changes)
    write_json(HERE / "reused-evidence-reviews.json", reused)
    print(
        {
            "outputs": len(rows),
            "reused_identical_sources": len(reused),
            "newly_inspected": len(inspected),
            "changes": len(changes),
            "verdicts": dict(Counter(r["verdict"] for r in rows)),
        }
    )


if __name__ == "__main__":
    main()
