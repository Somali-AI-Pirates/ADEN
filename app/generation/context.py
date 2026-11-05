from collections.abc import Callable, Sequence
from hashlib import sha256

from app.core.contracts import Citation, ContextBundle, RetrievedChunk


def build_context(
    candidates: Sequence[RetrievedChunk],
    k: int,
    token_budget: int,
    count_tokens: Callable[[str], int],
) -> ContextBundle:
    """검색 순서대로 청크를 고르고, 출처를 포함한 최종 문맥의 토큰 수를 확인한다."""
    if k <= 0 or token_budget <= 0:
        raise ValueError("최종 청크 수와 문맥 토큰 한도는 양수여야 합니다.")

    blocks: list[str] = []
    selected: list[RetrievedChunk] = []
    source_map: dict[str, Citation] = {}
    seen: set[str] = set()
    for candidate in candidates:
        chunk = candidate.chunk
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        source_id = f"S{len(selected) + 1}"
        heading = f"제목: {chunk.heading}\n" if chunk.heading else ""
        block = (
            f"[{source_id}]\n"
            f"문서 ID: {chunk.book_id}\n"
            f"문서명: {chunk.file_name}\n"
            f"PDF 페이지: {chunk.page}\n"
            f"{heading}내용: {chunk.text}"
        )
        proposed = "\n\n".join([*blocks, block])
        if count_tokens(proposed) > token_budget:
            continue
        source_map[source_id] = Citation(
            source_id=source_id,
            book_id=chunk.book_id,
            file_name=chunk.file_name,
            page=chunk.page,
        )
        blocks.append(block)
        selected.append(candidate)
        if len(selected) == k:
            break

    text = "\n\n".join(blocks)
    return ContextBundle(
        text=text,
        final_chunks=selected,
        source_map=source_map,
        context_hash=sha256(text.encode("utf-8")).hexdigest(),
        token_count=count_tokens(text),
    )
