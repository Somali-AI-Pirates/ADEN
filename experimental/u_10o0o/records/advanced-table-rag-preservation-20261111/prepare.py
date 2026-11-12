"""Repair missing question inputs in a new local copy; never emit answer values."""

import re
from pathlib import Path

from app.evaluation.dataset import load_references
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    read_json,
    write_json,
    object_sha,
    file_sha,
)

OLD = ROOT / "artifacts/u_10o0o/advanced-table-rag-peer-20261111/v2"
PREVIOUS = ROOT / "experimental/u_10o0o/records/advanced-table-rag-peer-20261111"
OUT = ROOT / "artifacts/u_10o0o/advanced-table-rag-preservation-20261111/v1"
RECORDS = Path(__file__).parent
CODE = re.compile(
    r"(?:HG\d{4}-\d+|HL-[A-Z0-9]+-\d+|GR\d{2}-[A-Z0-9-]+|P\d{2}-[A-Z0-9-]+)"
)
MANUAL = {
    "SYF3-adv-019": ["HL-40GP-01084", "HL-40GP-00922", "HL-40GP-01630"],
    "SYF3-adv-002": [
        f"GR27-N1-{n}" for n in (1633, 1644, 1655, 1666, 1677, 1688, 1699, 1710)
    ],
    "SYF3-adv-007": ["HL-20GP-00353", "HL-20GP-01799", "HL-20GP-01961"],
    "SYF3-adv-031": ["HG2029-00132", "HG2030-00486"],
    "SYF3-adv-039": ["HG2029-01983"],
    "SYF3-adv-041": ["HL-20GP-01669", "HL-20GP-01477", "HL-20GP-01315"],
    "SYF4-adv-007": ["HG2030-00826", "HG2030-02144", "HG2029-00344", "HG2030-01844"],
    "SYF4-adv-017": ["HG2030-00044", "HG2029-02198", "HG2029-00398", "HG2030-02036"],
    "SYF4-adv-020": ["HG2029-01474", "HG2028-00574", "HG2029-00274", "HG2029-00724"],
    "SYF4-adv-030": ["HL-40GP-00969", "HL-20GP-01434", "HL-40GP-02223"],
}


def main():
    if (OUT / "questions.json").exists():
        raise FileExistsError("Do not overwrite frozen questions")
    originals = {
        q["question_id"]: q
        for q in read_json(OLD / "questions.json")
        if q["split"] == "peer_multi_doc"
    }
    flags = read_json(PREVIOUS / "dataset-target-issues.json")
    ambiguous = {r["id"].split(":")[1] for r in flags if ":SYF" in r["id"]} | {
        "SYF3-adv-016"
    }
    repairs = ambiguous | {"SYF3-adv-002", "SYF3-adv-018", "SYF4-adv-030"}
    assert len(ambiguous) == 43 and len(repairs) == 46
    source = next(
        s
        for s in read_json(OLD / "protocol.json")["sources"]
        if s["split"] == "peer_multi_doc"
    )
    refs = load_references(ROOT / source["references"])
    pages = {
        (Path(p["file_name"]).stem, p["page"]): p["text"]
        for p in read_json(ROOT / "artifacts/u_10o0o/advanced_restored_pages.json")
    }
    questions, metadata, references = [], [], {}
    for qid, q in sorted(originals.items()):
        text = q["question"]
        ids = []
        reason = None
        if qid in repairs:
            if qid == "SYF3-adv-018":
                text = "HG2029-00220의 최초 계약 요율로 가정 계산을 부탁드립니다. 실제 사건 기록과는 별개로 양하 2029-11-05, 반출 2029-11-12, 지정 장치장 반납 2029-11-17이라고 가정합니다. 반납지연료는 반출 다음 날부터 세면 된다고 들었는데 맞나요? 계약상 산입 기준을 확인하고 보관료와 반납지연료 합계를 계산해 주세요."
                reason = "missing_calculation_input_and_hypothetical_scope"
            else:
                normal = originals[qid.replace("SYF", "SY", 1)]["question"]
                ids = MANUAL.get(qid, list(dict.fromkeys(CODE.findall(normal))))
                assert ids, qid
                source_text = "\n".join(
                    pages[(e["book_id"], e["page"])]
                    for g in refs[qid]["evidence_groups"]
                    for e in g
                )
                assert all(code in source_text for code in ids), (qid, ids)
                text = "대조할 대상은 " + ", ".join(ids) + "입니다. " + text
                reason = "restore_explicit_target_list_only"
                if qid == "SYF4-adv-030":
                    text += " 세 사건의 잔액을 각각 원문으로 확인하고, 서로 다른 기준시점의 단순 합산 참고액으로만 알려주세요."
                    reason = "restore_targets_and_define_actual_balance_scope"
            ref = dict(refs[qid])
            ref["question"] = text
            if qid == "SYF3-adv-018":
                ref["evidence_groups"] = [
                    [
                        e
                        for g in ref["evidence_groups"]
                        for e in g
                        if e["book_id"] == "AS016" and e["page"] == 85
                    ]
                ]
            references[q["id"]] = ref
        else:
            references[q["id"]] = refs[qid]
        questions.append(
            {
                "id": q["id"],
                "question_id": qid,
                "split": "peer_multi_doc",
                "question": text,
            }
        )
        metadata.append(
            {
                "id": q["id"],
                "kind": "false_premise" if qid.startswith("SYF") else "normal",
                "repaired": qid in repairs,
                "repair_reason": reason,
                "original_question_sha256": object_sha(q["question"]),
                "question_sha256": object_sha(text),
                "identifier_count": len(ids),
                "prior_ambiguous_normal": any(f["id"] == q["id"] for f in flags)
                and not qid.startswith("SYF"),
            }
        )
    for qid in sorted(ambiguous):
        q = originals[qid]
        rid = "clarify:" + qid
        questions.append(
            {
                "id": rid,
                "question_id": qid,
                "split": "clarify",
                "question": q["question"],
            }
        )
        metadata.append(
            {
                "id": rid,
                "kind": "clarification",
                "repaired": False,
                "question_sha256": object_sha(q["question"]),
            }
        )
    assert len(questions) == 213
    write_json(OUT / "questions.json", questions)
    write_json(OUT / "references.json", references)
    write_json(OUT / "question-metadata.json", metadata)
    write_json(
        RECORDS / "dataset-manifest.json",
        {
            "count": 213,
            "normal": 85,
            "false_premise": 85,
            "repaired": 46,
            "clarification": 43,
            "questions_sha256": file_sha(OUT / "questions.json"),
            "references_sha256": file_sha(OUT / "references.json"),
            "preparation_used_original_questions_and_references": True,
            "independent_human_review": False,
            "changes": metadata,
            "note": "Repaired development set, not unseen validation. No page IDs or answer values added to questions.",
        },
    )
    print(
        "Prepared 213 questions: 170 paired multi-document +43 clarification controls;46 repaired"
    )


if __name__ == "__main__":
    main()
