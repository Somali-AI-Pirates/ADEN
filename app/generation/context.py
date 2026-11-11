from collections.abc import Callable, Sequence
from hashlib import sha256

from shared.contracts import Chunk, Citation, ContextBundle, RetrievedChunk


def _context_block(chunk: Chunk, source_id: str) -> str:
    heading = f"제목: {chunk.heading}\n" if chunk.heading else ""
    return (
        f"[{source_id}]\n"
        f"문서 ID: {chunk.book_id}\n"
        f"문서명: {chunk.file_name}\n"
        f"PDF 페이지: {chunk.page}\n"
        f"{heading}내용: {chunk.text}"
    )


def validate_context(context: ContextBundle, count_tokens: Callable[[str], int]) -> None:
    """선택 순서·본문·출처·해시·토큰 수의 일치를 검사하며 문맥은 변경하지 않는다."""
    if not isinstance(context, ContextBundle):
        raise ValueError("최종 문맥 형식이 올바르지 않습니다.")
    expected_sources = {
        f"S{i}": Citation(source_id=f"S{i}", book_id=item.chunk.book_id,
                          file_name=item.chunk.file_name, page=item.chunk.page)
        for i, item in enumerate(context.final_chunks, 1)
    }
    if context.source_map != expected_sources:
        raise ValueError("최종 청크와 출처 목록이 일치하지 않습니다.")
    expected_text = "\n\n".join(
        _context_block(item.chunk, f"S{i}") for i, item in enumerate(context.final_chunks, 1)
    )
    if context.text != expected_text:
        raise ValueError("최종 청크와 문맥 본문이 일치하지 않습니다.")
    if context.context_hash != sha256(context.text.encode("utf-8")).hexdigest():
        raise ValueError("문맥 본문과 해시가 일치하지 않습니다.")
    if context.token_count != count_tokens(context.text):
        raise ValueError("문맥 본문과 토큰 수가 일치하지 않습니다.")


def build_context(
    candidates: Sequence[RetrievedChunk],
    k: int,
    token_budget: int | None,
    count_tokens: Callable[[str], int],
) -> ContextBundle:
    """검색 순서대로 청크를 고른다. token_budget이 None이면 청크 수로 제한한다."""
    if k <= 0 or (token_budget is not None and token_budget <= 0):
        raise ValueError("최종 청크 수와 문맥 토큰 한도는 양수여야 합니다.")

    blocks: list[str] = []
    selected: list[RetrievedChunk] = []
    source_map: dict[str, Citation] = {}
    seen: set[str] = set()
    excluded: list[str] = []
    separator_tokens = count_tokens("\n\n") if token_budget is not None else 0
    total_tokens = 0
    for candidate in candidates:
        chunk = candidate.chunk
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        source_id = f"S{len(selected) + 1}"
        block = _context_block(chunk, source_id)
        if token_budget is not None:
            added_tokens = count_tokens(block) + (separator_tokens if blocks else 0)
            if total_tokens + added_tokens > token_budget:
                excluded.append(chunk.chunk_id)
                continue
            total_tokens += added_tokens
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
    final_tokens = count_tokens(text)
    while token_budget is not None and selected and final_tokens > token_budget:
        source_map.pop(f"S{len(selected)}")
        excluded.append(selected.pop().chunk.chunk_id)
        blocks.pop()
        text = "\n\n".join(blocks)
        final_tokens = count_tokens(text)
    return ContextBundle(
        text=text,
        final_chunks=selected,
        source_map=source_map,
        context_hash=sha256(text.encode("utf-8")).hexdigest(),
        token_count=final_tokens,
        excluded_chunk_ids=excluded,
    )
