"""규칙 기반 질문 재작성 + 다중 질문 검색 (개인 실험, 제품 코드 아님).

문제: 질문 속 구체 조건(장비 코드·날짜·항로)에 끌려, 조건이 적힌 사례 페이지만 찾고
      "공통 마감은 없다" 같은 일반 규칙 페이지를 놓친다 (예: S-FINAL-27).
방법: 원래 질문은 그대로 두고, 조건을 뺀 일반 질문을 하나 더 만든다.
      두 질문으로 각각 hybrid 후보(Dense 20 + BM25 20 → RRF 20)를 뽑아 합치고,
      리랭커는 '원래 질문' 기준으로 전체를 다시 정렬한다. 날짜처럼 답을 가르는 조건은
      리랭커와 답변 단계가 원래 질문으로 계속 보므로 버려지지 않는다.
"""
import re

from app.retrieval.dense import dense_search
from app.retrieval.hybrid import CANDIDATES, rrf_merge
from app.retrieval.reranker import rerank
from app.retrieval.service import keyword_index_for, reranker_for
from shared.contracts import RetrievedChunk

# 지울 조건. 순서대로 적용한다.
RULES = [
    ("날짜", r"(\d{4}\s*년\s*)?\d{1,2}\s*월\s*\d{1,2}\s*일"),
    ("시각", r"(오전|오후|낮|밤)?\s*\d{1,2}\s*시(\s*\d{1,2}\s*분)?"),
    ("장비 코드", r"(?<![A-Za-z0-9])\d{2}\s?(GP|HC|RF|FR|OT|DC)(?![A-Za-z0-9])"),  # "40GP로"처럼 조사가 붙어도
    ("대수", r"(한|두|세|\d+)\s*대"),
    ("항로", r"부산\s*[–\-~]\s*LA"),
    ("선사명", r"가온루트(해운)?(의)?"),
]
_COMPILED = [(name, re.compile(p, re.IGNORECASE)) for name, p in RULES]


def rewrite(question):
    """(일반 질문 또는 None, 지운 조건 이름 목록). 지울 게 없으면 None."""
    text, removed = question, []
    for name, pattern in _COMPILED:
        if pattern.search(text):
            removed.append(name)
            text = pattern.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not removed or len(text) < 8:
        return None, removed
    return text, removed


def _hybrid_chunks(query, resources, keyword_index, n=CANDIDATES):
    dense = [item.chunk for item in dense_search(query, resources, n)]
    keyword = [chunk for chunk, _ in keyword_index.search(query, n)]
    return [item.chunk for item in rrf_merge([dense, keyword], n)]


def retrieve_qr(question, resources, top_n=20, generic=None, fuse=False):
    """hybrid_rerank + 질문 재작성. 반환 형식은 retrieve()와 같다.

    generic  일반 질문을 직접 줄 때(예: LLM 재작성). 없으면 규칙 재작성.
    fuse     True면 리랭커를 원래 질문·일반 질문으로 각각 돌려 두 순위를 RRF로 합친다.
             False면 원래 질문으로만 리랭크한다(v1).
    """
    keyword_index = keyword_index_for(resources)
    if generic is None:
        generic, _ = rewrite(question)
    if generic and generic.strip() == question.strip():
        generic = None
    pool, seen = [], set()
    for query in [question] + ([generic] if generic else []):
        for chunk in _hybrid_chunks(query, resources, keyword_index):
            if chunk.chunk_id not in seen:
                seen.add(chunk.chunk_id)
                pool.append(chunk)
    candidates = [RetrievedChunk(chunk=c, rank=i, score=0.0, retriever="hybrid_qr") for i, c in enumerate(pool, 1)]
    reranker = reranker_for(resources)
    if not (fuse and generic):
        return rerank(question, candidates, reranker, top_n)
    by_question = rerank(question, candidates, reranker, len(candidates))
    by_generic = rerank(generic, candidates, reranker, len(candidates))
    fused = rrf_merge([[x.chunk for x in by_question], [x.chunk for x in by_generic]], top_n)
    return [RetrievedChunk(chunk=x.chunk, rank=x.rank, score=x.score, retriever="reranker_qr") for x in fused]


LLM_PROMPT = (
    "너는 문서 검색용 질문 재작성기다. 사용자의 질문에서 특정 예약을 가리키는 구체 조건"
    "(날짜, 시각, 장비 코드, 대수, 항로, 회사명, 번호)을 빼고, 질문이 실제로 묻는 업무 규칙이나 절차를 "
    "한국어 한 문장의 일반 질문으로 바꿔라. 질문에 없는 사실이나 답을 덧붙이지 마라. "
    "여러 가지를 물으면 핵심 규칙을 모두 담되 한 문장으로 쓴다. 재작성한 질문 한 문장만 출력한다."
)


def llm_rewrite(question, client, model):
    """LLM으로 일반 질문을 만든다. 실패하면 None (원래 질문만 쓰게 된다)."""
    resp = client.chat.completions.create(
        model=model, max_completion_tokens=200,
        messages=[{"role": "system", "content": LLM_PROMPT}, {"role": "user", "content": question}])
    text = (resp.choices[0].message.content or "").strip().splitlines()
    return text[0].strip() if text else None
