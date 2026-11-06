from collections.abc import Mapping, Sequence

from shared.contracts import Citation


def resolve_citations(
    source_ids: Sequence[str],
    source_map: Mapping[str, Citation],
) -> list[Citation]:
    """최종 문맥에 있는 자료 ID를 원본 문서와 페이지로 연결한다."""
    citations: list[Citation] = []
    for source_id in dict.fromkeys(source_ids):
        if source_id not in source_map:
            raise ValueError(f"최종 검색 문맥에 없는 출처입니다. {source_id}")
        citations.append(source_map[source_id])
    return citations
