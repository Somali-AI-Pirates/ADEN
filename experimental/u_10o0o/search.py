import json
from functools import cache

import numpy as np

from experimental.u_10o0o.embeding import EMBEDDING_DIM, VECTORS_PATH, load_model
from experimental.u_10o0o.prepare import CACHE_PATH

TOP_K = 3


@cache
def load_index():
    pages = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    if not VECTORS_PATH.is_file():
        raise FileNotFoundError(
            "Snowflake-ko 문서 벡터를 먼저 만드세요. "
            "uv run python -m experimental.u_10o0o.main"
        )
    vectors = np.load(VECTORS_PATH, allow_pickle=False)
    if vectors.shape != (len(pages), EMBEDDING_DIM):
        raise ValueError(
            "저장된 문서 벡터가 페이지 수 또는 Snowflake-ko 벡터 차원과 다릅니다."
        )
    return pages, vectors


def rank_pages(
    question_vector, document_vectors, pages, *, top_k: int = TOP_K
) -> list[tuple[dict, float]]:
    return top_pages(document_vectors @ question_vector, pages, top_k=top_k)


def top_pages(scores, pages, *, top_k: int = TOP_K) -> list[tuple[dict, float]]:
    top_indices = scores.argsort()[::-1][:top_k]
    return [(pages[int(index)], float(scores[index])) for index in top_indices]


def search(question: str, *, top_k: int = TOP_K) -> list[tuple[dict, float]]:
    pages, vectors = load_index()
    question_vector = load_model().encode_query(question, normalize_embeddings=True)
    return rank_pages(question_vector, vectors, pages, top_k=top_k)


if __name__ == "__main__":
    question = "예약이 확정됐는지 어디서 확인하나요?"
    for page, score in search(question):
        print(f"{page['file_name']} {page['page']}쪽 | {score:.3f}")
