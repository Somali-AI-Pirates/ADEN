"""Follow-up questions through SSE, the real graph and the answer prompt."""

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.api.main import create_app
from app.observability.tracing import Tracer
from app.rag.model import TableModel
from app.rag.models import SearchResult, SourceRef, make_document
from app.rag.service import RagService


class ModelInputs(BaseCallbackHandler):
    def __init__(self):
        self.calls = []

    def on_chat_model_start(self, serialized, messages, *, name=None, **kwargs):
        self.calls.append((name.split("-")[0], messages[0]))


class FakeProvider(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self.bind(tools=tools, **kwargs)


class OfflineModel(TableModel):
    async def aclose(self):
        pass


def text_response(**value):
    return AIMessage(content=json.dumps(value, ensure_ascii=False))


def decision(status, *, search_question="", turn_ids=()):
    return text_response(
        status=status, search_question=search_question, turn_ids=list(turn_ids)
    )


class Records:
    def __init__(self):
        self.identifiers = []

    def lookup(self, targets):
        [target] = targets
        [code] = target["identifiers"]
        self.identifiers.append(code)
        return {
            "targets": [
                {
                    "status": "matched",
                    "truncated": False,
                    "candidate_count": 1,
                    "entities": [
                        {
                            "id": code,
                            "family": "HG",
                            "kind": "booking",
                            "label": "테스트 예약",
                            "sources": [
                                {
                                    "source": "AS001:p0001",
                                    "role": "invoice",
                                    "ordinal": 1,
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    def close(self):
        pass


class Tables:
    catalog = {}
    by_page = {}

    def schema(self, table_ids):
        assert not table_ids
        return []


@pytest.mark.parametrize(
    (
        "question",
        "history",
        "interpretations",
        "windows",
        "selected",
        "query",
        "expected_status",
    ),
    [
        (
            "그 건 청구액은?",
            [
                {"question": "이전의 무관한 규정은?", "answer": "별도 규정입니다."},
                {"question": "HG2029-00001 건의 처리일은?", "answer": "5월 4일입니다."},
                {"question": "이 건 상태는?", "answer": "접수됐습니다."},
            ],
            [
                decision(
                    "resolved",
                    search_question="HG2029-00001 건 청구액은?",
                    turn_ids=[2],
                )
            ],
            [[2, 3]],
            1,
            "HG2029-00001 건 청구액은?",
            "answered",
        ),
        (
            "처음 물었던 건의 청구액은?",
            [
                {"question": "HG2029-00002 건의 처리일은?", "answer": "5월 8일입니다."},
                {"question": "일반 규정은?", "answer": "일반 규정입니다."},
                {"question": "일반 절차는?", "answer": "일반 절차입니다."},
            ],
            [
                decision("needs_history"),
                decision(
                    "resolved",
                    search_question="HG2029-00002 건 청구액은?",
                    turn_ids=[1],
                ),
            ],
            [[2, 3], [1, 2, 3]],
            0,
            "HG2029-00002 건 청구액은?",
            "answered",
        ),
        (
            "그 건 청구액은?",
            [{"question": "두 건을 비교해줘", "answer": "두 건이 조회됐습니다."}],
            [decision("needs_clarification", turn_ids=[1])],
            [[1]],
            0,
            None,
            "needs_clarification",
        ),
        (
            "그 건 청구액은?",
            [{"question": "HG2029-00002 건은?", "answer": "접수됐습니다."}],
            [
                decision(
                    "resolved",
                    search_question="HG2029-99999 건 청구액은?",
                    turn_ids=[1],
                )
            ],
            [[1]],
            0,
            None,
            "needs_clarification",
        ),
        (
            "이번에는 HG2029-00003 건 청구액은?",
            [{"question": "HG2029-00002 건은?", "answer": "접수됐습니다."}],
            [
                decision(
                    "resolved",
                    search_question="HG2029-00002 건 청구액은?",
                    turn_ids=[1],
                )
            ],
            [[1]],
            0,
            None,
            "needs_clarification",
        ),
    ],
    ids=[
        "recent",
        "older-on-demand",
        "ambiguous",
        "ungrounded-code",
        "preserve-current-code",
    ],
)
def test_follow_up_through_sse_and_model(
    tmp_path,
    question,
    history,
    interpretations,
    windows,
    selected,
    query,
    expected_status,
):
    source_id = "AS001:p0001"
    page = make_document(
        "청구 금액 1,200원",
        SourceRef(book_id="AS001", page=1, file_name="AS001.pdf"),
        "AS001:p0001:page",
    )
    records = Records()
    inputs = ModelInputs()
    outputs = list(interpretations)
    if query:
        outputs.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "finish",
                        "id": "finish",
                        "args": {
                            "status": "sufficient",
                            "selected_sources": [source_id],
                            "interim_summary": "",
                            "conflicts": [],
                        },
                    }
                ],
            )
        )
    outputs.append(
        text_response(
            answer="1,200원입니다." if query else "어느 예약 건을 말씀하시나요?",
            status=expected_status,
            citations=[source_id] if query else [],
        )
    )
    gateway = OfflineModel(FakeProvider(messages=iter(outputs), callbacks=[inputs]))
    hybrid_queries = []

    def search(query):
        hybrid_queries.append(query)
        return SearchResult((page,), (), {})

    def service(_):
        return RagService(
            search=search,
            page_lookup={source_id: page}.get,
            table_store=Tables(),
            record_index=records,
            model=gateway,
            tracer=Tracer(),
            manifest={},
            run_directory=tmp_path,
        )

    with TestClient(create_app(table_service_loader=service)) as client:
        response = client.post(
            "/query/stream", json={"question": question, "history": history}
        )
    assert response.status_code == 200
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    final = events[-1]["response"]
    assert final["status"] == expected_status
    assert final["citations"] == (
        [
            {
                "source_id": source_id,
                "book_id": "AS001",
                "page": 1,
                "file_name": "AS001.pdf",
            }
        ]
        if query
        else []
    )
    assert hybrid_queries == []
    assert records.identifiers == ([query.split()[0]] if query else [])
    assert [kind for kind, _ in inputs.calls] == (
        ["conversation"] * len(windows) + (["planner"] if query else []) + ["answer"]
    )
    for (_, messages), turn_ids in zip(inputs.calls, windows, strict=False):
        submitted = json.loads(messages[-1].content)
        assert [item["turn_id"] for item in submitted["turns"]] == turn_ids
    if query:
        planner_input = json.loads(inputs.calls[-2][1][-1].content)
        assert planner_input["question"] == query

    answer_messages = inputs.calls[-1][1]
    assert [message.type for message in answer_messages] == [
        "system",
        "human",
        "ai",
        "human",
    ]
    assert [message.content for message in answer_messages[1:3]] == [
        history[selected]["question"],
        history[selected]["answer"],
    ]
    answer_payload = json.loads(answer_messages[-1].content)
    assert answer_payload["question"] == question
    assert answer_payload.get("resolved_question") == query
    assert len(answer_payload["evidence"]["pages"]) == bool(query)
    record = json.loads((tmp_path / final["request_id"] / "runs.jsonl").read_text())
    assert record["question"] == question
    assert json.loads(record["model_context_text"]) == answer_payload["evidence"]
    assert len(record["attempts"][-1]["model_input"]["conversation_history"]) == 2
