"""Apply source-inspected scope/quote corrections; retain raw model decisions."""

from copy import deepcopy
from importlib import import_module

from experimental.u_10o0o.pattern_retrieval.common import read_json, write_json

m = import_module(
    "experimental.u_10o0o.records.advanced-table-rag-request-evidence-20261112.analyze"
).analyzer()


def main():
    path = m.OUT / "adjudications.json"
    backup = m.OUT / "adjudications-model.json"
    if backup.exists():
        raise FileExistsError("Preserve completed adjudication")
    rows = read_json(path)
    write_json(backup, deepcopy(rows))
    inputs = {(r["id"], r["arm"]): r for r in read_json(m.OUT / "review-inputs.json")}
    index = {(r["id"], r["arm"]): r for r in rows}
    changes = []

    def row(q, arm):
        return index[
            (
                "official_development:" + q
                if q.startswith("AS-DEV")
                else "official_additional:" + q,
                arm,
            )
        ]

    def raw(q, arm, source):
        r = row(q, arm)
        return next(
            p["text"]
            for p in inputs[(r["id"], arm)]["context"]
            if f"{p['book_id']}:p{p['page']:04d}" == source
        )

    def para(q, arm, source, contains):
        return next(p for p in raw(q, arm, source).split("\n\n") if contains in p)

    def proof(source, quote):
        return {"source_id": source, "quote": quote}

    def change(q, arm, reason):
        r = row(q, arm)
        changes.append({"id": r["id"], "arm": arm, "change": reason})

    def sufficient(q, arm, reason):
        r = row(q, arm)
        r.update(
            verdict="sufficient", scope_ok=True, reason=reason, public_reason=reason
        )
        change(q, arm, reason)

    # Readable table cells were split/interleaved by PDF extraction.
    q, arm = "AS-DEV06", "B"
    c = row(q, arm)["claims"][1]
    c.update(
        status="supported",
        evidence=[
            proof("AS009:p0005", para(q, arm, "AS009:p0005", "IS03-005-01")),
            proof("AS009:p0005", para(q, arm, "AS009:p0005", "광진수출 건의")),
        ],
        reason="원문 행에 최초 접수와 제출처가 함께 있다. 끊긴 주소를 새 인용으로 합치지 않고 열이 섞인 원문 행 자체를 인용했다.",
    )
    sufficient(
        q,
        arm,
        "SI 최초 접수 기록의 제출처와 VGM 신규 경로·중량 구분이 실제 반환 문맥에 있다.",
    )

    # A and B both have the same applicable general deadline rule.
    q, arm = "AS-ADD02", "A"
    c = row(q, arm)["claims"][1]
    c.update(
        status="supported",
        evidence=[proof("AS014:p0001", para(q, arm, "AS014:p0001", "이 문서는 선사"))],
        reason="질문은 출항일 변경이 확정된 경우의 준비 마감을 묻는다. 반환된 동일 서비스의 적용 범위와 이틀 전 정오 규칙으로 계산 가능하며 개별 예약의 완료 상태를 주장하지 않는다.",
    )
    sufficient(
        q,
        arm,
        "예약 변경 요청 내용과 조건부 SI 마감 규칙이 반환되어 있다. B에도 같은 범위 기준을 적용했다.",
    )

    for arm in ("A", "B"):
        q = "AS-ADD03"
        row(q, arm)["claims"] = [
            {
                "fact": "취소 요청을 보내고 회신이 없는 상태만으로 취소 완료라고 기록할 수 없다.",
                "status": "supported",
                "evidence": [
                    proof("AS008:p0031", para(q, arm, "AS008:p0031", "GR26-P0365는"))
                ],
                "reason": "요청 접수·미회신과 취소 완료를 구분하는 반환 원문이 질문의 판단을 직접 지원한다. 사용자의 특정 예약이 이 사례라는 주장은 하지 않는다.",
            }
        ]
        sufficient(
            q,
            arm,
            "미회신 취소 요청을 완료 처리하지 않는 구분 근거가 있다. 질문 밖 특정 담당자 확인 절차를 별도 필수 요구로 확대하지 않았다.",
        )
        q = "AS-ADD10"
        row(q, arm)["claims"] = [
            {
                "fact": "기존 이메일 접수 완료 건을 포털에 신규·중복 등록할 필요가 없다.",
                "status": "supported",
                "evidence": [
                    proof("AS014:p0019", para(q, arm, "AS014:p0019", "20GP VGM은"))
                ],
                "reason": "질문은 신규 포털 등록 여부다. 별도 이메일 정정 절차 전체는 질문이 요구하지 않았다.",
            }
        ]
        sufficient(q, arm, "기존 접수분의 포털 중복 등록 제외 규칙이 질문에 충분하다.")
        q = "AS-ADD14"
        row(q, arm)["claims"] = [
            c for c in row(q, arm)["claims"] if c["status"] == "supported"
        ]
        sufficient(
            q,
            arm,
            "질문이 요청한 승인 적용 보관료와 무료·유료 날짜가 반환 정산 원문에 명시되어 있다. 원시 사건일의 별도 증명까지 필수로 확대하지 않았다.",
        )

    # Correct a partial claim without hiding the actual missing requirement.
    q, arm = "AS-ADD06", "A"
    c = row(q, arm)["claims"][1]
    c.update(
        status="supported",
        evidence=[proof("AS007:p0008", para(q, arm, "AS007:p0008", "IS01-008-06"))],
        reason="SI 중량 정정 담당과 연락 창구를 같은 원문 행에서 확인했다.",
    )
    row(q, arm)["claims"][0]["reason"] = (
        "다른 날짜의 20GP 신규 이메일 접수 사례는 있지만, 질문의 신규 접수일에 적용할 경로 규정은 직접 확인되지 않는다. 개별 사례를 적용기간 전체의 규정으로 대체하지 않는다."
    )
    row(q, arm).update(
        reason="SI 정정 창구와 중량 구분은 확인되지만, 요청한 날짜의 20GP 신규 VGM 경로 적용 근거가 부족하다.",
        public_reason="20GP 신규 VGM의 해당 날짜 적용 경로 근거 부족.",
    )
    change(
        q,
        arm,
        "SI 정정 연락처의 추출 순서 문제를 교정했다. 날짜별 신규 VGM 경로의 누락은 실패로 유지했다.",
    )

    q, arm = "AS-ADD09", "B"
    c = row(q, arm)["claims"][2]
    c.update(
        status="supported",
        evidence=[proof("AS011:p0001", para(q, arm, "AS011:p0001", "GR26-P0833은"))],
        reason="반환한 봉인 보완 처리 기록에 기존·새 번호 요청이 명시되어 있다.",
    )
    row(q, arm).update(
        reason="SI·반입팀 변경 통지와 기존·새 번호, VGM 신규 경로는 있으나 운송사에도 변경 정보를 전달하는 근거가 빠졌다.",
        public_reason="봉인 변경을 운송사에도 전달하는 근거 부족.",
    )
    change(
        q,
        arm,
        "기존·새 번호의 대체 근거를 인정했다. 운송사 변경 통지 누락은 실패로 유지했다.",
    )

    # Replace invalid/too-short quotes with their original continuous row/header.
    for q, arm, source in [
        ("AS-DEV20", "B", "AS054:p0107"),
        ("AS-DEV26", "A", "AS078:p0114"),
        ("AS-DEV26", "B", "AS078:p0114"),
        ("AS-ADD21", "B", "AS073:p0071"),
        ("AS-ADD23", "A", "AS112:p0098"),
    ]:
        text = raw(q, arm, source)
        changed = False
        for c in row(q, arm)["claims"]:
            for e in c["evidence"]:
                if e["source_id"] != source:
                    continue
                if len(m.norm(e["quote"])) >= 8 and m.norm(e["quote"]) in m.norm(text):
                    continue
                if q in ("AS-DEV26", "AS-ADD21"):
                    start = text.index("항목 보관료")
                    e["quote"] = text[start : text.index("전액 면제", start)].strip()
                else:
                    e["quote"] = next(
                        p for p in text.split("\n\n") if p.startswith("장비반납지연료 ")
                    )
                changed = True
        if changed:
            change(
                q,
                arm,
                "표 셀·머리말의 잘못되거나 짧은 인용을 실제 연속 원문 행으로 교체했다. 판정은 유지했다.",
            )

    row("AS-DEV06", "A")["public_reason"] = "SI 최초 제출 창구의 직접 근거 부족."
    for r in rows:
        r["assistant_checked"] = True
        r["review_type"] = (
            "All-output AI evidence review plus assistant source/scope inspection; no independent human review"
        )
    write_json(path, rows)
    write_json(m.RECORDS / "assistant-review-changes.json", changes)
    write_json(
        m.OUT / "assistant-inspected.json", [r["id"] + "|" + r["arm"] for r in rows]
    )
    print(
        {
            "outputs": len(rows),
            "corrections": len(changes),
            "sufficient": sum(r["verdict"] == "sufficient" for r in rows),
        }
    )


if __name__ == "__main__":
    main()
