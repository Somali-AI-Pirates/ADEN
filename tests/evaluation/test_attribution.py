import json

from app.evaluation.attribution import (
    diagnose_failures,
    parse_fact_checks,
)
from app.rag.models import SearchResult
from app.rag.result import retrieval_observations_record
from langchain_core.documents import Document


def _record(*, status="answered", finish_status="sufficient"):
    return {
        "question": "비용은 얼마인가요?",
        "status": status,
        "answer": "비용은 200원입니다.",
        "reason": "최종 문맥을 확인했습니다.",
        "diagnostics": {"finish_status": finish_status},
        "final_context_chunks": [
            {
                "source_id": "AS001:p0001",
                "book_id": "AS001",
                "page": 1,
                "text": "확정 비용은 100원입니다.",
            }
        ],
    }


def _fact_checks(*, final_support="supported", answer_status="wrong", quote="100원"):
    return {
        "fact_checks": [
            {
                "fact_id": "F1",
                "fact": "확정 비용은 100원",
                "origin": "reference",
                "final_support": final_support,
                "answer_status": answer_status,
                "supporting_evidence": [{"source_id": "AS001:p0001", "quote": quote}]
                if final_support == "supported"
                else [],
                "answer_quote": "200원"
                if answer_status in ("correct", "wrong")
                else "",
                "reason": "검증 예시",
            }
        ]
    }


def test_parse_fact_checks_requires_exact_reference_fact_and_verified_quotes():
    record = _record()
    reference = {"required_facts": ["확정 비용은 100원"]}

    result = parse_fact_checks(_fact_checks(), record, reference)

    assert result["status"] == "scored"
    assert result["items"][0]["attribution"] == "answer_generation_miss"
    assert result["items"][0]["supporting_evidence"] == [
        {"source_id": "AS001:p0001", "quote": "100원"}
    ]


def test_unverified_final_quote_cannot_be_counted_as_supported():
    result = parse_fact_checks(
        _fact_checks(quote="금액은 900원"),
        _record(),
        {"required_facts": ["확정 비용은 100원"]},
    )

    assert result["status"] == "scored"
    assert result["items"][0]["final_support"] == "uncertain"
    assert result["items"][0]["attribution"] == "uncertain"
    assert result["items"][0]["supporting_evidence"] == []


def test_non_sufficient_retrieval_gate_is_not_blame_assigned_to_answer_model():
    result = parse_fact_checks(
        _fact_checks(answer_status="missing"),
        _record(status="needs_clarification", finish_status="needs_clarification"),
        {"required_facts": ["확정 비용은 100원"]},
    )

    assert result["items"][0]["attribution"] == "retrieval_status_gate"


def test_missing_legacy_finish_status_does_not_invent_a_gate():
    record = _record(status="needs_clarification", finish_status=None)
    result = parse_fact_checks(
        _fact_checks(), record, {"required_facts": ["확정 비용은 100원"]}
    )

    assert result["items"][0]["attribution"] == "answer_generation_miss"


def test_supported_and_correct_fact_is_not_marked_uncertain():
    result = parse_fact_checks(
        _fact_checks(answer_status="correct", quote="100원"),
        _record(),
        {"required_facts": ["확정 비용은 100원"]},
    )

    assert result["items"][0]["attribution"] == "supported"


def test_abstention_without_factual_requirements_has_no_fact_checks():
    result = parse_fact_checks(
        {"fact_checks": []},
        _record(status="abstained"),
        {"expected_action": "abstain"},
    )

    assert result["status"] == "not_applicable"


def test_missing_legacy_observations_are_unavailable_not_a_retrieval_miss():
    checks = parse_fact_checks(
        _fact_checks(final_support="unsupported", answer_status="missing"),
        _record(),
        {"required_facts": ["확정 비용은 100원"]},
    )

    result = diagnose_failures(_record(), {}, checks, lambda _: {})

    assert result["status"] == "unavailable"
    assert "not saved" in result["reason"]


def test_candidate_diagnosis_uses_late_observation_without_top20_truncation():
    passages = []
    all_ids = []
    for event in range(3):
        event_passages = []
        event_ids = []
        for page in range(event * 8 + 1, event * 8 + 9):
            source_id = f"AS001:p{page:04d}"
            text = (
                "늦게 찾은 근거는 100원입니다." if page == 24 else f"일반 페이지 {page}"
            )
            event_ids.append(source_id)
            event_passages.append(
                {"source_id": source_id, "text": text, "excerpt_truncated": False}
            )
        all_ids.extend(event_ids)
        passages.extend(event_passages)

    record = _record()
    record["retrieval_observations"] = {
        "schema": "retrieval-observations-v1",
        "source_ids": all_ids,
        "search_results": [
            {
                "index": 1,
                "kind": "documents",
                "source_ids": all_ids,
                "pages": [
                    {
                        "source_id": f"AS001:p{page:04d}",
                        "text": "늦게 찾은 근거는 100원입니다."
                        if page == 24
                        else f"전체 검색 결과 {page}",
                    }
                    for page in range(1, 25)
                ],
            }
        ],
        "items": [
            {
                "index": 1,
                "kind": "documents",
                "source_ids": all_ids[:8],
                "ids_only_source_ids": [],
                "passages": passages[:8],
                "record_resolution": None,
                "tables": [],
                "sql": None,
                "sql_in_final_context": False,
            },
            {
                "index": 2,
                "kind": "documents",
                "source_ids": all_ids[8:16],
                "ids_only_source_ids": [],
                "passages": passages[8:16],
                "record_resolution": None,
                "tables": [],
                "sql": None,
                "sql_in_final_context": False,
            },
            {
                "index": 3,
                "kind": "documents",
                "source_ids": all_ids[16:],
                "ids_only_source_ids": [],
                "passages": passages[16:],
                "record_resolution": None,
                "tables": [],
                "sql": None,
                "sql_in_final_context": False,
            },
        ],
    }
    record["retrieved_candidates"] = [
        {"source_id": f"AS001:p{page:04d}", "text": f"병합 후보 {page}"}
        for page in range(1, 21)
    ]
    captured = {}

    def fake_judge(prompt):
        captured["prompt"] = prompt
        return {
            "text": json.dumps(
                {
                    "items": [
                        {
                            "fact_id": "F1",
                            "search_result_support": "supported",
                            "search_result_evidence": [
                                {"source_id": "AS001:p0024", "quote": "100원입니다"}
                            ],
                            "planner_observation_support": "supported",
                            "planner_observation_evidence": [
                                {"source_id": "AS001:p0024", "quote": "100원입니다"}
                            ],
                            "merged_candidate_support": "unsupported",
                            "merged_candidate_evidence": [],
                            "reason": "끝쪽 관측에 근거가 있습니다.",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            "input_tokens": 100,
            "output_tokens": 30,
        }

    checks = parse_fact_checks(
        _fact_checks(final_support="unsupported", answer_status="missing"),
        record,
        {"required_facts": ["확정 비용은 100원"]},
    )
    result = diagnose_failures(record, {}, checks, fake_judge)

    assert result["status"] == "scored"
    assert result["items"][0]["failure_stage"] == "candidate_merge_loss"
    assert "AS001:p0024" in captured["prompt"]
    assert "늦게 찾은 근거는 100원입니다" in captured["prompt"]
    assert "병합 후보 1" in captured["prompt"]


def test_trace_records_full_id_union_and_actual_passages_separately():
    source_ids = [f"AS001:p{page:04d}" for page in range(1, 22)]
    observation = {
        "kind": "documents",
        "candidate_sources": source_ids,
        "passages": [
            {
                "source_id": source_id,
                "text": f"실제 발췌 {index}",
                "excerpt_truncated": False,
            }
            for index, source_id in enumerate(source_ids[:8], 1)
        ],
        "tables": [],
        "subquery": "질문",
    }
    sql = {
        "status": "ok",
        "rows": [{"amount": "100"}],
        "sources": [{"book_id": "AS001", "page": 1}],
    }
    search_pages = tuple(
        Document(
            page_content=f"원문 페이지 {page}",
            metadata={
                "source_id": source_id,
                "book_id": "AS001",
                "page": page,
                "file_name": "AS001.pdf",
                "unit_id": source_id,
            },
        )
        for page, source_id in enumerate(source_ids, 1)
    )
    search = SearchResult(search_pages, (), {"kind": "documents"})

    result = retrieval_observations_record((observation,), (sql,), (search,))

    assert result["schema"] == "retrieval-observations-v1"
    assert result["source_ids"] == source_ids
    assert result["items"][0]["ids_only_source_ids"] == source_ids[8:]
    assert len(result["items"][0]["passages"]) == 8
    assert len(result["search_results"][0]["pages"]) == 21
    assert result["search_results"][0]["pages"][-1]["source_id"] == "AS001:p0021"
    assert result["items"][0]["sql_in_final_context"] is False
    sql_observation = {
        "kind": "sql",
        "source_ids": ["AS001:p0001"],
        "passages": [],
        "sql": sql,
    }
    final = retrieval_observations_record((sql_observation,), (sql,))
    assert final["items"][0]["sql_in_final_context"] is True


def test_retrieval_gate_does_not_hide_wrong_or_correct_partial_claims():
    held = _record(status="needs_clarification", finish_status="needs_clarification")
    reference = {"required_facts": ["확정 비용은 100원"]}
    wrong = parse_fact_checks(_fact_checks(answer_status="wrong"), held, reference)
    assert wrong["items"][0]["attribution"] == "answer_generation_miss"
    held["answer"] = "비용은 100원입니다. 나머지 항목은 추가 확인이 필요합니다."
    correct = _fact_checks(answer_status="correct")
    correct["fact_checks"][0]["answer_quote"] = "100원"
    right = parse_fact_checks(correct, held, reference)
    assert right["items"][0]["attribution"] == "supported"


def test_pure_clarification_without_required_facts_is_not_applicable():
    for reference in ({"expected_action": "clarify"}, {"expected_action": "clarify", "required_facts": []}):
        assert parse_fact_checks(
            {"fact_checks": []}, _record(status="needs_clarification"), reference,
        )["status"] == "not_applicable"
