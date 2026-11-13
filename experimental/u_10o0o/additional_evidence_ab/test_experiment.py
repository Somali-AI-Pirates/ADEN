import json

import tiktoken
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from .policy import with_previews
from .evaluate import Review, validate_review, exact


def test_only_discovered_unseen_sources_get_previews():
    documents = {f"S{i}": Document(page_content=f"원문 {i}") for i in range(22)}
    message = HumanMessage(
        content=json.dumps(
            {
                "initial_evidence": [
                    {
                        "candidate_sources": [f"S{i}" for i in range(20)],
                        "passages": [
                            {"source_id": f"S{i}", "text": f"원문 {i}"}
                            for i in range(8)
                        ],
                    }
                ]
            }
        )
    )
    before = message.content
    messages, audit = with_previews(
        [message], documents.get, tiktoken.get_encoding("o200k_base")
    )
    previews = json.loads(messages[0].content)["initial_evidence"][0][
        "candidate_previews"
    ]
    assert [p["source_id"] for p in previews] == [f"S{i}" for i in range(8, 20)]
    assert all(p["preview_only"] for p in previews)
    assert len(audit) == 12
    assert message.content == before


def test_unknown_candidates_are_not_invented_and_full_reads_suppress_preview():
    message = HumanMessage(
        content=json.dumps(
            {
                "candidate_sources": ["known", "absent"],
                "pages": [{"source_id": "known", "text": "원문"}],
            }
        )
    )
    result, audit = with_previews(
        [message], lambda _: None, tiktoken.get_encoding("o200k_base")
    )
    assert not audit
    assert "candidate_previews" not in json.loads(result[0].content)


def test_judge_cannot_add_requirements_or_invent_quotes():
    job = {
        "required": [{"id": "R1", "fact": "대상 확인"}],
        "final_evidence": [
            {"source_id": "A", "text": "예약 확인 전에는 완료가 아니다."}
        ],
    }
    review = Review.model_validate(
        {
            "scope_ok": True,
            "reason": "지원",
            "claims": [
                {
                    "requirement_id": "R1",
                    "status": "supported",
                    "reason": "명시 규칙",
                    "evidence": [
                        {"source_id": "A", "quote": "예약 확인 전에는 완료가 아니다."}
                    ],
                }
            ],
        }
    )
    assert validate_review(review, job) == []
    review.claims[0].evidence[0].source_id = "not-returned"
    assert validate_review(review, job) == ["R1:quote_not_in_context"]
    review.claims[0].requirement_id = "R2"
    assert validate_review(review, job) == ["requirement_ids_differ"]


def test_designated_page_score_keeps_book_and_page():
    groups = [[{"book_id": "A", "page": 1}], [{"book_id": "B", "page": 2}]]
    assert exact(
        [{"book_id": "A", "page": 1}, {"book_id": "A", "page": 2}], groups
    ) == {"all_groups": False, "page_recall": 0.5}
