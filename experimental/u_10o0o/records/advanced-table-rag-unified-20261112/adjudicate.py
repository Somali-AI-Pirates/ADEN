"""Record assistant adjudication after inspecting all 84 model reviews and flagged contexts.

Only final returned evidence is used. No retrieval output or reference is rewritten.
"""

from pathlib import Path
import re
import unicodedata

from experimental.u_10o0o.pattern_retrieval.common import ROOT, read_json, write_json

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v3"


def norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold()


def main():
    path = OUT / "adjudications.json"
    original = read_json(path)
    backup = OUT / "adjudication-drafts.json"
    if backup.exists():
        raise FileExistsError(
            "Assistant adjudication already started; inspect before rerunning"
        )
    write_json(backup, original)
    items = read_json(OUT / "review-inputs.json")
    rows = {(r["id"], r["arm"]): r for r in original}
    changes = []

    def row(i):
        q = items[i]
        return rows[q["id"], q["arm"]]

    def paragraph(i, source, *needles):
        page = next(
            p
            for p in items[i]["context"]
            if f"{p['book_id']}:p{p['page']:04d}" == source
        )
        found = next(
            t for t in page["text"].split("\n\n") if all(n in t for n in needles)
        )
        return {"source_id": source, "quote": found}

    def record(i, reason):
        r = row(i)
        r["assistant_notes"] = reason
        changes.append({"id": r["id"], "arm": r["arm"], "change": reason})

    # Correct model quotations against the exact returned text, without changing verdicts.
    row(7)["claims"][1]["evidence"][1] = paragraph(
        7, "AS013:p0007", "실제 진행 회신 대기 건"
    )
    record(7, "문장 끝을 바꿔 인용한 부분을 실제 반환 문장의 연속 인용으로 교체했다.")
    row(17)["claims"][0]["evidence"][1] = paragraph(17, "AS078:p0114", "정액 감액")
    # This actual table line is shorter than the quote validator's minimum. Include its preceding heading.
    p = next(
        p for p in items[17]["context"] if p["book_id"] == "AS078" and p["page"] == 114
    )
    begin = p["text"].index("승인 후 조정")
    end = p["text"].index("전액 면제", begin)
    row(17)["claims"][0]["evidence"][1]["quote"] = p["text"][begin:end].strip()
    record(
        17,
        "원문과 일치하지만 너무 짧은 표 셀 인용에 실제 표 머리말과 앞 행을 함께 보존했다.",
    )
    row(45)["claims"][1]["evidence"][0] = paragraph(45, "AS014:p0019", "20GP VGM은")
    record(45, "원문에 없는 쉼표가 삽입된 인용을 반환 원문 그대로 교체했다.")
    row(70)["claims"][1]["evidence"] = [
        paragraph(70, "AS024:p0001", "계약 갱신은"),
        paragraph(70, "AS024:p0019", "출항 일정 변경은"),
    ]
    record(
        70,
        "다른 자료의 문구처럼 인용한 부분을 실제 반환된 계약 적용 기준과 기존 예약 존속 문장으로 교체했다.",
    )

    # The question does not request a department name, an extra submission procedure,
    # or an unrequested historical event date. Keep only what answers the actual request.
    keep = {24: [0], 25: [0], 30: [1], 31: [2], 32: [0, 1, 2, 3], 37: [1, 2]}
    for i, selected in keep.items():
        r = row(i)
        r["claims"] = [r["claims"][j] for j in selected]
        r.update(verdict="sufficient", scope_ok=True)
        record(
            i,
            "질문 밖 추가 요구를 필수조건으로 삼지 않았다. 반환 근거가 실제 질문의 판단·금액·기간 또는 적용 범위를 충족한다.",
        )
    row(37)["claims"][0]["evidence"].append(
        paragraph(37, "AS073:p0071", "정산실 대조번호")
    )
    row(37)["assistant_notes"] = (
        "질문이 요구한 상한 범위·비용·합계는 동일 화주·화물·수량·조회시각·계약의 원문에 있다. 양하일 자체를 답하라는 별도 요구로 확대하지 않았다."
    )

    # The PDF text interleaves an equipment identifier into one email cell.
    # Preserve the actual row as proof and disclose reconstruction, rather than inventing an unbroken quote.
    r = row(27)
    r["claims"][1].update(
        status="supported",
        evidence=[paragraph(27, "AS007:p0019", "SI 중량 정정", "docs@")],
        reason="같은 표 행에서 이메일 사이에 끼어든 장비 식별자 열을 구분하면 주소를 재구성할 수 있다. 해당 행의 업무도 SI 중량 정정으로 일치한다.",
    )
    r.update(verdict="sufficient", scope_ok=True)
    record(
        27,
        "반환 표 행의 열 섞임은 주소 단서 누락과 구분했다. 행 자체를 인용하며 이어 붙인 주소를 원문 인용처럼 만들지 않았다.",
    )

    # Both questions ask whether an already-submitted item must be sent through the new portal.
    # The returned transition notice directly excludes those duplicate submissions.
    for i in (44, 61):
        r = row(i)
        r["claims"][1].update(
            fact="기존 이메일 접수분은 신규 접수 경로 전환만을 이유로 새 포털에 중복 제출하지 않는다.",
            status="supported",
            evidence=[paragraph(i, "AS014:p0019", "20GP VGM은")],
            reason="신규 접수에 한정된 변경과 기접수분의 포털 중복 제출 금지로 질문의 구분에 답할 수 있다.",
        )
        r.update(verdict="sufficient", scope_ok=True)
        record(
            i,
            "질문의 기접수·신규접수 구분에 직접 대응하는 반환 공지를 인정했다. 특정 건에서 이미 정정 처리가 끝났다고 주장하지 않는다.",
        )

    r = row(77)
    r["claims"][1].update(
        status="supported",
        evidence=[
            paragraph(77, "AS022:p0007", "추가 무료기간은 승인서가"),
            paragraph(77, "AS017:p0039", "기본 무료기간과 추가 승인"),
        ],
        reason="추가 무료일은 지정 장비에 승인된 일수만 적용하고, 금액 감액은 무료일수 산출 뒤 별도로 반영한다. 질문이 다른 장비에는 금액 감액만 있다고 명시하므로 무료일 승인을 전용하지 않는다.",
    )
    r.update(verdict="sufficient", scope_ok=True)
    record(
        77,
        "지정 장비·승인 일수 한정 규칙과 금액 감액의 별도 계산을 함께 인정했다. 동일 문구가 있는 특정 정답 쪽만 요구하지 않았다.",
    )

    for i in (68, 69):
        r = row(i)
        r.update(verdict="ambiguous_question", scope_ok=False)
        r["claims"][0]["status"] = "ambiguous"
        r["claims"][0]["reason"] = (
            "질문에 항만·적용 계약 범위가 없고 Advanced에는 양하일 산입 방식이 다른 조건이 있어 한 규칙으로 확정할 수 없다."
        )
        record(
            i,
            "기존에 지적된 적용 범위 모호성을 재확인했다. 양쪽 모두 분모에 남기고 성공으로 세지 않는다.",
        )

    reasons = {
        "AS-DEV02": "최초 예약 신청 창구의 직접 근거가 부족하며 예약 변경 연락처로 대신했다.",
        "AS-DEV06": "VGM 접수처와 중량 구분은 확보했으나 SI 최초 제출 창구가 빠졌다.",
        "AS-DEV09": "SI 정정 메일의 예약 식별정보·첨부자료 등 필수 제출 내용이 부족하다.",
        "AS-ADD02": "예약 변경 요청에 포함할 정보와 절차 원문이 부족하다.",
        "AS-WR-20": "특정 요율 문의의 메모 처리 사례만 있고 내부 청구 준비 메모의 일반 효력 근거가 부족하다.",
        "AS-SEM-21": "반입 준비 정보는 있으나 예약 접수와 확정 회신을 구별하는 근거가 빠졌다.",
        "AS-SEM-22": "중량 정정은 확보했으나 장비 배정 변경의 요청 정보·절차가 빠졌다.",
        "AS-SEM-23": "중량 항목 구분은 있으나 신규 대형 장비의 VGM 포털 제출 안내가 빠졌다.",
        "AS-SEM-28": "일정 변경 전달은 확보했으나 목록 등록일과 업무값 효력을 구별하는 규칙이 빠졌다.",
        "AS-SEM-30": "비용 조정의 미승인 상태는 있으나 월말 인계 단계별 기록 기준이 부족하다.",
        "AS-SEM-31": "SI 기한과 반입 회신은 있으나 기존 VGM 정정 경로가 빠졌다.",
        "AS-SEM-35": "개별 무료기간 계약이 기본표를 대체하는 규칙이 빠졌다.",
        "AS-SEM-37": "감액 대체는 확보했으나 미발행 계산 초안과 유효 발행 통지의 관계가 빠졌다.",
    }
    for i in range(len(items)):
        r = row(i)
        r["assistant_checked"] = True
        if r["verdict"] == "sufficient":
            r["public_reason"] = (
                "동일 대상 또는 해당 적용 범위의 반환 원문으로 질문의 필수 요구를 충족한다."
            )
        elif r["verdict"] == "ambiguous_question":
            r["public_reason"] = (
                "적용 항만·계약을 특정하지 않아 보관일수 산입 규칙을 하나로 확정할 수 없다."
            )
        else:
            r["public_reason"] = reasons[r["id"].split(":")[-1]]
        if "assistant_notes" not in r:
            r["assistant_notes"] = (
                "질문 범위·주장·인용 및 해당 대상/조건의 일치를 대조했다."
            )
        # Every proof must really occur in the returned source; no gold quotation is inserted.
        sources = {
            f"{p['book_id']}:p{p['page']:04d}": p["text"] for p in items[i]["context"]
        }
        for c in r["claims"]:
            for proof in c["evidence"]:
                if proof["source_id"].startswith("SQL"):
                    continue
                assert len(norm(proof["quote"])) >= 8
                assert norm(proof["quote"]) in norm(sources[proof["source_id"]]), (
                    i,
                    proof,
                )
        if r["verdict"] == "sufficient":
            assert r["scope_ok"] and all(
                c["status"] == "supported" for c in r["claims"]
            )
    write_json(path, original)
    write_json(HERE / "assistant-review-changes.json", changes)
    print({"checked": len(original), "amended_outputs": len(changes)})


if __name__ == "__main__":
    main()
