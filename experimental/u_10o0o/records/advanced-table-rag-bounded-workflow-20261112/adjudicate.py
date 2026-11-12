"""Recorded source/scope corrections; preserve model drafts and distinguish review methods."""

from importlib import import_module
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import ROOT, read_json, write_json

HERE = Path(__file__).parent
OUT = ROOT / "artifacts/u_10o0o" / HERE.name / "v2"


def main():
    m = import_module(
        "experimental.u_10o0o.records.advanced-table-rag-unified-20261112.analyze"
    )
    rows = read_json(OUT / "adjudications.json")
    by = {(r["id"], r["arm"]): r for r in rows}
    items = {(r["id"], r["arm"]): r for r in read_json(OUT / "review-inputs.json")}
    changes = []
    for r in rows:
        r.setdefault(
            "verification_mode",
            "reused_assistant" if r["assistant_checked"] else "model_source_checked",
        )

    def correct(rid, arm, reason):
        r = by[rid, arm]
        changes.append(
            {"id": rid, "arm": arm, "before": r["verdict"], "reason": reason}
        )
        r["assistant_checked"] = True
        r["verification_mode"] = "assistant_scope_or_citation_correction"
        r["public_reason"] = reason
        return r

    # The prescribed task is to obtain both ambiguous candidates, not force a unique numeric answer.
    for r in rows:
        if (
            r["id"].startswith("scenarios:AS-S06-")
            and r["verdict"] == "ambiguous_question"
        ):
            assert r["arm"] == "A" and all(
                c["status"] == "supported" for c in r["claims"]
            )
            target = correct(
                r["id"],
                r["arm"],
                "추가 확인형은 두 후보의 식별 기록과 금액을 확보하면 근거 충족이다. 하나의 금액을 확정할 수 없다는 이유로 검색 실패로 판정하지 않는다.",
            )
            target.update(verdict="sufficient", scope_ok=True)

    def replace_proof(rid, arm, claim, proof, source, quote):
        row = correct(
            rid,
            arm,
            "실제로 반환된 원문 위치·연속 문구로 인용을 교정했다. 새 출처나 참고답을 근거에 추가하지 않았다.",
        )
        row["claims"][claim]["evidence"][proof] = {"source_id": source, "quote": quote}

    replace_proof(
        "work_rules:AS-WR-07",
        "A",
        0,
        0,
        "AS012:p0035",
        "초안 승인 회신에는 승인하는 판본을 명시한다. 담당자가 파일을 받았다고 답하거나 읽음 표시가 나타난 것만으로 기재 내용의 승인이라고 처리하지 않는다.",
    )
    replace_proof(
        "official_additional:AS-ADD18",
        "B",
        2,
        1,
        "AS119:p0042",
        "두 비용 합계는 USD 760이다.",
    )
    quote = "정정 대상은 실제 반출일 4월 11일로 바로잡는 보관료 입력값이다."
    item = items["official_development:AS-DEV13", "B"]
    sources = [
        f"{p['book_id']}:p{p['page']:04d}"
        for p in item["context"]
        if m.norm(quote) in m.norm(p["text"])
    ]
    assert len(sources) == 1
    replace_proof("official_development:AS-DEV13", "B", 3, 1, sources[0], quote)
    replace_proof(
        "official_development:AS-DEV02",
        "B",
        3,
        0,
        "AS014:p0007",
        "GR26-P0671은 11월 6일 예정으로 11월 4일 정오까지 docs@gaonroute.example에 SI를 제출하도록 되어 있습니다.",
    )

    r = correct(
        "semantic_queries:AS-SEM-29",
        "B",
        "질문은 특정 금액의 산출이 아니라 초안·발행 구분과 미배분금 관리 원칙을 묻는다. 반환 원문의 명시적 관리 지침이 두 요청을 지원한다. 다른 사례의 금액을 옮기지 않는다.",
    )
    r.update(
        verdict="sufficient",
        scope_ok=True,
        claims=[
            {
                "fact": "새 초안 수신만으로 발행 상태가 바뀌지 않으며, 전달 시 대조 중 값과 발행 원문으로 확인한 값을 구분한다.",
                "status": "supported",
                "reason": "원문에 업무 상태와 안내 기준이 명시되어 있다.",
                "evidence": [
                    {
                        "source_id": "AS026:p0028",
                        "quote": "초안 전달이나 근거 대조가 발행 상태를 바꾸지는 않습니다.",
                    },
                    {
                        "source_id": "AS045:p0059",
                        "quote": "전달 전에 대조중인 값과 발행 원문으로 확인된 값을 나누어 안내해야 합니다.",
                    },
                ],
            },
            {
                "fact": "승인 배분한 금액과 남은 미배분 금액을 구분하고, 남은 금액은 청구에 임의로 반영하지 않는다.",
                "status": "supported",
                "reason": "부분 배분 후 잔액의 관리 지침을 그대로 사용하며 사례 금액을 전용하지 않는다.",
                "evidence": [
                    {
                        "source_id": "AS053:p0020",
                        "quote": "부분 배분액과 무입금 기록을 구분해 후속 관리하며, 미배분액을 청구에 임의로 돌리지 않는다.",
                    }
                ],
            },
        ],
    )

    for r in rows:
        item = items[r["id"], r["arm"]]
        sources = {
            f"{p['book_id']}:p{p['page']:04d}": p["text"] for p in item["context"]
        }
        sources.update(
            {f"SQL{i}": m.json_payload(s) for i, s in enumerate(item["sql"])}
        )
        for c in r["claims"]:
            if c["status"] == "supported":
                assert c["evidence"]
            for e in c["evidence"]:
                if e["source_id"] in sources and len(m.norm(e["quote"])) < 8:
                    raw = sources[e["source_id"]]
                    chars = [(ch, i) for i, ch in enumerate(raw) if not ch.isspace()]
                    folded = "".join(ch for ch, _ in chars).casefold()
                    at = folded.find(m.norm(e["quote"]))
                    assert at >= 0
                    start = chars[at][1]
                    end = chars[at + len(m.norm(e["quote"])) - 1][1] + 1
                    e["quote"] = raw[max(0, start - 45) : min(len(raw), end + 45)]
                    changes.append(
                        {
                            "id": r["id"],
                            "arm": r["arm"],
                            "before": r["verdict"],
                            "reason": "짧은 표 셀 인용의 앞뒤 원문을 함께 보존했다. 판정과 출처는 유지했다.",
                        }
                    )
                assert e["source_id"] in sources and len(m.norm(e["quote"])) >= 8, (
                    r["id"],
                    r["arm"],
                    e["source_id"],
                    len(m.norm(e["quote"])),
                )
                assert m.norm(e["quote"]) in m.norm(sources[e["source_id"]]), (
                    r["id"],
                    r["arm"],
                    e["source_id"],
                )
        if r["verdict"] == "sufficient":
            assert (
                r["scope_ok"]
                and r["claims"]
                and all(c["status"] == "supported" for c in r["claims"])
            )
        r["source_validation_complete"] = True
    for c in changes:
        c["after"] = by[c["id"], c["arm"]]["verdict"]
    write_json(OUT / "adjudications.json", rows)
    write_json(HERE / "assistant-review-changes.json", changes)
    print(
        {
            "scope_or_citation_corrections": len(changes),
            "verdict_changes": sum(c["before"] != c["after"] for c in changes),
            "reviewed_or_reused_by_assistant": sum(
                r["assistant_checked"] for r in rows
            ),
            "model_plus_source_checks": sum(not r["assistant_checked"] for r in rows),
        }
    )


if __name__ == "__main__":
    main()
