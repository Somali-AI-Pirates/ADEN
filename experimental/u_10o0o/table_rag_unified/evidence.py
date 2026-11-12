"""Optional request-to-source checks; no evaluation references or answer generation."""

import re
from unicodedata import normalize
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from experimental.u_10o0o.table_rag.agent import Finish
from experimental.u_10o0o.table_rag.retrieval import source_id


class Proof(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    quote: str = Field(min_length=8, max_length=700)


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question_span: str = Field(min_length=2, max_length=700)
    requirement: str = Field(min_length=2, max_length=300)
    status: Literal["supported", "missing", "ambiguous"]
    evidence: list[Proof] = Field(max_length=4)


class EvidenceFinish(Finish):
    requirements: list[Requirement] = Field(min_length=1, max_length=12)


INSTRUCTIONS = """
요청별 근거 확인:
- 질문에서 사용자가 실제 요구한 내용을 대상별·행동별로 나눠 requirements에 모두 기록한다.
  question_span은 질문의 실제 연속 구절이다. requirement는 그 구절이 요구한 확인 사항이다.
  참고답은 제공되지 않는다. 질문이 요청하지 않은 상세사항을 새 필수 요구로 만들지 않는다.
- 같은 업무의 최초 접수와 정정, 요청과 승인, 제출처와 담당자, 예정과 실제를 구분한다.
  여러 요구가 있으면 첫 요구의 근거만으로 전체가 충분하다고 끝내지 않는다.
- 먼저 현재 passages가 각 요구를 직접 뒷받침하는지 확인한다.
  빠진 항목은 unread_candidates의 탐색용 미리보기를 보고 관련 source_id를 read_sources로 읽는다.
  이미 발견한 후보에 관련 원문이 있으면 동일 문서를 다시 찾는 검색보다 원문 읽기를 먼저 한다.
  미리보기는 위치를 고르는 안내이며 근거 인용으로 사용하지 않는다. 읽어도 부족할 때 후속 검색한다.
- supported 항목마다 실제 passages에서 읽은 연속 원문 인용과 source_id를 넣는다.
  단어 몇 개의 일치보다 대상·행동·적용 범위를 지원하는 문장이나 표 행을 인용한다.
  SQL 계산은 원문 피연산자·단위·범위 근거를 인용한다. 계산값을 원문 인용으로 만들지 않는다.
- 충분 종료는 모든 requirements가 supported이고 서로 충돌하지 않을 때만 가능하다.
  인용한 모든 출처를 selected_sources에 넣고 최종 최대8쪽에 각 요구의 근거가 남게 선택한다.
  일부 요구가 미해결이면 missing/ambiguous를 남기고 insufficient/needs_clarification으로 끝낸다.
- 중간 요약·후보 미리보기·출처 ID 자체는 사실의 근거가 아니다.
"""


def compact(text):
    return re.sub(r"\s+", "", normalize("NFKC", text))


def preview_candidates(result, observation):
    """Point to unread candidates without replacing full source reading."""
    enc = __import__("tiktoken").get_encoding("o200k_base")
    seen = {p["source_id"] for p in observation.get("passages", [])}
    observation["unread_candidates"] = [
        {
            "source_id": source_id(page),
            "rank": rank,
            "preview_not_evidence": enc.decode(enc.encode(page["text"])[:80]),
        }
        for rank, (page, _) in enumerate(result["ranked"], 1)
        if source_id(page) not in seen
    ]
    return observation


def validate_requirements(finish, question, observations, final_pages=None):
    """Validate grounding/retention, not semantic completeness of the model's list."""
    errors = []
    visible = {}
    for obs in observations:
        for passage in obs.get("passages", []):
            visible.setdefault(passage["source_id"], []).append(passage["text"])
    retained = None
    if final_pages is not None:
        retained = {source_id(page): compact(page["text"]) for page in final_pages}
    selected = set(finish.selected_sources)
    for i, req in enumerate(finish.requirements):
        if compact(req.question_span) not in compact(question):
            errors.append(f"requirement {i}: question_span not in question")
        if req.status == "supported" and not req.evidence:
            errors.append(f"requirement {i}: supported requires source quotation")
        if finish.status == "sufficient" and req.status != "supported":
            errors.append(f"requirement {i}: unresolved request cannot be sufficient")
        for proof in req.evidence:
            quote = compact(proof.quote)
            if len(quote) < 8 or not any(
                quote in compact(text) for text in visible.get(proof.source_id, [])
            ):
                errors.append(
                    f"requirement {i}: quotation not in read passages; use read_sources"
                )
            if proof.source_id not in selected:
                errors.append(
                    f"requirement {i}: quoted source missing from selected_sources"
                )
            if retained is not None and quote not in retained.get(proof.source_id, ""):
                errors.append(f"requirement {i}: quotation absent from final context")
    return list(dict.fromkeys(errors))
