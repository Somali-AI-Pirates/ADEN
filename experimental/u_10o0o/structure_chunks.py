"""Prepare page-local, heading-aware chunks for the Advanced PDF experiment."""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Any

from experimental.u_10o0o.pdf import restore_paragraph
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR, DATASET, PROJECT_ROOT

CHUNKS_PATH = PROJECT_ROOT / "artifacts/u_10o0o/advanced_structure_chunks.json"
METADATA_PATH = (
    PROJECT_ROOT / "artifacts/u_10o0o/advanced_structure_chunks_metadata.json"
)
TOKEN_LIMIT = 448
KURE_MODEL_ID = "nlpai-lab/KURE-Reranker-nano"
KURE_REVISION = "8514b9e113f3b27c441ed9c2b50f81d80388b2fd"


@dataclass
class Section:
    heading_blocks: list[str] = field(default_factory=list)
    leading_blocks: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)

    @property
    def heading(self) -> str:
        return "\n\n".join(self.heading_blocks)


class DocumentTokenizer:
    """Use the cached KURE tokenizer without loading the reranker model."""

    def __init__(self) -> None:
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer

        snapshot = snapshot_download(
            repo_id=KURE_MODEL_ID,
            revision=KURE_REVISION,
            local_files_only=True,
        )
        self.tokenizer = AutoTokenizer.from_pretrained(
            snapshot,
            local_files_only=True,
        )
        self._count_cache: dict[str, int] = {}

    def count(self, text: str) -> int:
        cached = self._count_cache.get(text)
        if cached is None:
            cached = len(self.tokenizer.encode(text, add_special_tokens=False))
            self._count_cache[text] = cached
        return cached

    def split_by_tokens(self, text: str, limit: int) -> list[str]:
        if not text:
            return [text]
        encoded = self.tokenizer(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
        )
        offsets: list[tuple[int, int]] = encoded["offset_mapping"]
        if len(offsets) <= limit:
            return [text]

        parts: list[str] = []
        char_start = 0
        for token_start in range(0, len(offsets), limit):
            token_end = min(token_start + limit, len(offsets))
            char_end = offsets[token_end - 1][1]
            if token_end < len(offsets):
                # Keep each fragment on a tokenizer boundary and retain every
                # intervening character, including whitespace and punctuation.
                char_end = max(char_end, offsets[token_end][0])
            char_end = max(char_end, char_start)
            parts.append(text[char_start:char_end])
            char_start = char_end
        if char_start < len(text):
            parts[-1] += text[char_start:]
        return [part for part in parts if part] or [text]


def _normalize_for_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _font_spans(page: Any) -> list[tuple[str, float, bool]]:
    spans: list[tuple[str, float, bool]] = []

    def visitor_text(text, cm, tm, font_dict, font_size) -> None:
        if not text or not text.strip() or not font_dict:
            return
        font_name = str(font_dict.get("/BaseFont", "")).casefold()
        is_bold = any(name in font_name for name in ("bold", "black", "demi", "heavy"))
        normalized = _normalize_for_match(text)
        if normalized:
            spans.append((normalized, float(font_size or 0), is_bold))

    page.extract_text(visitor_text=visitor_text)
    return spans


def _heading_block_indexes(page: Any, blocks: list[str]) -> set[int]:
    """Mark only blocks dominated by large, bold PDF text spans.

    Layout extraction remains the source of all emitted text. Visitor spans are
    used only as a typography signal and must match canonical block characters.
    """
    spans = _font_spans(page)
    regular_sizes = [size for _, size, bold in spans if size > 0 and not bold]
    if not regular_sizes:
        return set()

    body_size = median(regular_sizes)
    title_spans = [
        text
        for text, size, bold in spans
        if bold and size >= max(12.0, body_size * 1.25) and len(text) >= 3
    ]
    if not title_spans:
        return set()

    heading_indexes: set[int] = set()
    for block_index, block in enumerate(blocks):
        normalized_block = _normalize_for_match(block)
        if not normalized_block:
            continue

        covered = bytearray(len(normalized_block))
        for title_span in title_spans:
            start = 0
            while (match_start := normalized_block.find(title_span, start)) >= 0:
                match_end = match_start + len(title_span)
                covered[match_start:match_end] = b"\x01" * len(title_span)
                start = match_end

        if sum(covered) / len(normalized_block) >= 0.6:
            heading_indexes.add(block_index)

    return heading_indexes


def _sections(blocks: list[str], heading_indexes: set[int]) -> list[Section]:
    sections: list[Section] = []
    current = Section()
    for index, block in enumerate(blocks):
        if index in heading_indexes:
            if current.heading_blocks and not current.paragraphs:
                current.heading_blocks.append(block)
            elif not sections and not current.heading_blocks:
                # Initial page metadata remains in source order and joins the
                # first real heading's text, rather than becoming its own hit.
                current.heading_blocks.append(block)
                current.leading_blocks = current.paragraphs
                current.paragraphs = []
            else:
                if current.heading_blocks or current.paragraphs:
                    sections.append(current)
                current = Section(heading_blocks=[block])
        else:
            current.paragraphs.append(block)
    if current.heading or current.paragraphs:
        sections.append(current)
    return sections


def _sentence_parts(text: str) -> list[str]:
    """Split at sentence punctuation while retaining exact source substrings."""
    boundaries = [match.end() for match in re.finditer(r"(?<=[.!?。！？])(?=\s)", text)]
    if not boundaries:
        return [text]

    parts: list[str] = []
    start = 0
    for boundary in boundaries:
        parts.append(text[start:boundary])
        start = boundary
    parts.append(text[start:])
    return [part for part in parts if part]


def _fit_paragraph(
    text: str,
    prefix: str,
    tokenizer: DocumentTokenizer,
    diagnostics: Counter[str],
) -> list[str]:
    available = max(1, TOKEN_LIMIT - tokenizer.count(prefix))
    if tokenizer.count(text) <= available:
        return [text]

    diagnostics["over_limit_paragraphs"] += 1
    sentence_parts = _sentence_parts(text)
    units: list[str] = []
    for sentence in sentence_parts:
        if tokenizer.count(sentence) <= available:
            units.append(sentence)
            continue
        token_parts = tokenizer.split_by_tokens(sentence, available)
        if len(token_parts) > 1:
            diagnostics["token_split_fragments"] += len(token_parts)
        units.extend(token_parts)

    if "".join(units) != text:
        raise ValueError("긴 문단을 나눈 뒤 원문 문자 보존 검사가 실패했습니다.")
    return units


def _chunk_section(
    section: Section,
    *,
    file_name: str,
    page_number: int,
    tokenizer: DocumentTokenizer,
    diagnostics: Counter[str],
) -> list[dict[str, Any]]:
    leading = "\n\n".join(section.leading_blocks)
    if section.heading:
        prefix = (
            f"{leading}\n\n{section.heading}\n" if leading else f"{section.heading}\n"
        )
    else:
        prefix = leading
    chunks: list[dict[str, Any]] = []
    body_parts: list[tuple[str, bool]] = []
    body_paragraph_count = 0
    chunk_index = 0

    def emit() -> None:
        nonlocal body_parts, body_paragraph_count, chunk_index
        if not body_parts and not section.heading:
            return
        body = "".join(
            ("\n\n" if is_paragraph_start and index else "") + part
            for index, (part, is_paragraph_start) in enumerate(body_parts)
        )
        text = f"{prefix}{body}" if body else prefix.rstrip()
        token_count = tokenizer.count(text)
        if token_count > TOKEN_LIMIT:
            raise ValueError(f"청크 토큰 상한 초과: {file_name}, page {page_number}")
        chunks.append(
            {
                "chunk_id": f"{Path(file_name).stem}-p{page_number}-c{chunk_index}",
                "file_name": file_name,
                "page": page_number,
                "heading": section.heading,
                "text": text,
            }
        )
        diagnostics["max_document_tokens"] = max(
            diagnostics["max_document_tokens"], token_count
        )
        diagnostics["body_paragraphs_in_chunks"] += body_paragraph_count
        chunk_index += 1
        body_parts = []
        body_paragraph_count = 0

    if not section.paragraphs:
        emit()
        return chunks

    for paragraph in section.paragraphs:
        units = _fit_paragraph(paragraph, prefix, tokenizer, diagnostics)
        if len(units) > 1:
            diagnostics["split_paragraphs"] += 1

        for unit_index, unit in enumerate(units):
            is_paragraph_start = unit_index == 0
            candidate_parts = [*body_parts, (unit, is_paragraph_start)]
            candidate_body = "".join(
                ("\n\n" if starts_paragraph and index else "") + part
                for index, (part, starts_paragraph) in enumerate(candidate_parts)
            )
            candidate_text = f"{prefix}{candidate_body}" if prefix else candidate_body
            if body_parts and tokenizer.count(candidate_text) > TOKEN_LIMIT:
                emit()
                candidate_body = unit
                candidate_text = f"{prefix}{unit}" if prefix else unit

            if tokenizer.count(candidate_text) > TOKEN_LIMIT:
                # This is only expected for a very long heading prefix. Preserve
                # the source paragraph as an unheaded body instead of truncating.
                if section.heading:
                    fallback = Section(paragraphs=[unit])
                    chunks.extend(
                        _chunk_section(
                            fallback,
                            file_name=file_name,
                            page_number=page_number,
                            tokenizer=tokenizer,
                            diagnostics=diagnostics,
                        )
                    )
                    diagnostics["heading_prefix_overflow_fallbacks"] += 1
                    continue
                raise ValueError(
                    f"문장/토큰 분할 후에도 상한 초과: {file_name}, page {page_number}"
                )

            body_parts.append((unit, is_paragraph_start))
            body_paragraph_count += 1

    emit()
    return chunks


def _load_page_cache() -> list[dict[str, Any]]:
    if not CACHE_PATH.is_file():
        from experimental.u_10o0o.prepare import main as prepare_pages

        prepare_pages()

    pages = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    if not isinstance(pages, list) or not pages:
        raise ValueError(f"페이지 캐시가 비어 있거나 배열이 아닙니다: {CACHE_PATH}")
    return pages


def build_chunks() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if DATASET != "advanced":
        raise SystemExit("구조 청크는 Advanced PDF 실험에만 지원합니다.")

    page_cache = _load_page_cache()
    cached_by_key = {
        (page["file_name"], page["page"]): page["text"] for page in page_cache
    }
    page_counts = Counter(page["file_name"] for page in page_cache)
    tokenizer = DocumentTokenizer()
    diagnostics: Counter[str] = Counter()
    chunks: list[dict[str, Any]] = []
    source_blocks_seen = 0
    pages_with_heading = 0
    headings_found = 0
    cache_mismatches = 0
    page_count = 0
    excluded_footer_blocks = 0
    source_blocks_before_footer_filter = 0

    from pypdf import PdfReader

    for pdf_path in sorted((DATA_DIR / "documents").glob("*.pdf")):
        reader = PdfReader(pdf_path)
        for page_number, pdf_page in enumerate(reader.pages, start=1):
            page_count += 1
            layout_text = pdf_page.extract_text(extraction_mode="layout") or ""
            layout_blocks = [
                restore_paragraph(block)
                for block in re.split(r"\n\s*\n", layout_text.strip())
                if block.strip()
            ]

            cached_text = cached_by_key.get((pdf_path.name, page_number))
            if cached_text is None:
                raise ValueError(
                    f"페이지 캐시에 원본이 없습니다: {pdf_path.name}, {page_number}"
                )
            cached_blocks = [block for block in cached_text.split("\n\n") if block]
            source_blocks_before_footer_filter += len(cached_blocks)
            if layout_blocks != cached_blocks:
                cache_mismatches += 1
                # The ignored page cache is the text source of truth. If current
                # extraction differs, retain its text and avoid unreliable joins.
                layout_blocks = cached_blocks
                heading_indexes: set[int] = set()
            else:
                heading_indexes = _heading_block_indexes(pdf_page, layout_blocks)

            if layout_blocks and _is_page_footer(
                layout_blocks[-1],
                page_number=page_number,
                page_count=page_counts[pdf_path.name],
            ):
                layout_blocks = layout_blocks[:-1]
                heading_indexes.discard(len(layout_blocks))
                excluded_footer_blocks += 1

            # A page-final large-title block with no body on the same page is
            # page continuation metadata; retain it as body text, not a hit by itself.
            for heading_index in sorted(heading_indexes, reverse=True):
                if not any(
                    index > heading_index and index not in heading_indexes
                    for index in range(len(layout_blocks))
                ):
                    heading_indexes.discard(heading_index)

            sections = _sections(layout_blocks, heading_indexes)
            reconstructed = [
                block
                for section in sections
                for block in section.leading_blocks
                + section.heading_blocks
                + section.paragraphs
            ]
            if reconstructed != layout_blocks:
                raise ValueError(
                    "본문 블록 보존 검사가 실패했습니다: "
                    f"{pdf_path.name}, {page_number}"
                )

            source_blocks_seen += len(layout_blocks)
            if heading_indexes:
                pages_with_heading += 1
                headings_found += len(heading_indexes)

            page_chunks: list[dict[str, Any]] = []
            for section in sections:
                page_chunks.extend(
                    _chunk_section(
                        section,
                        file_name=pdf_path.name,
                        page_number=page_number,
                        tokenizer=tokenizer,
                        diagnostics=diagnostics,
                    )
                )
            if not page_chunks and cached_text.strip():
                raise ValueError(
                    f"본문이 청크로 변환되지 않았습니다: {pdf_path.name}, {page_number}"
                )
            for chunk_index, chunk in enumerate(page_chunks):
                chunk["chunk_id"] = (
                    f"{Path(pdf_path.name).stem}-p{page_number}-c{chunk_index}"
                )
            chunks.extend(page_chunks)

    if page_count != len(page_cache):
        raise ValueError(
            f"PDF 페이지 수와 페이지 캐시 행 수가 다릅니다: "
            f"{page_count} != {len(page_cache)}"
        )

    identifiers = [chunk["chunk_id"] for chunk in chunks]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("청크 ID가 중복됩니다.")

    return chunks, {
        "dataset": "advanced",
        "pages": page_count,
        "source_paragraph_blocks": source_blocks_seen,
        "source_blocks_before_footer_filter": source_blocks_before_footer_filter,
        "source_body_block_retention": (
            f"{source_blocks_before_footer_filter - excluded_footer_blocks}/"
            f"{source_blocks_before_footer_filter} blocks; only numeric page-number "
            "footers excluded"
        ),
        "page_number_footer_blocks_excluded": excluded_footer_blocks,
        "chunks": len(chunks),
        "pages_with_heading": pages_with_heading,
        "heading_blocks": headings_found,
        "pages_without_heading": page_count - pages_with_heading,
        "cache_extraction_mismatches": cache_mismatches,
        "source_block_reconstruction": "passed",
        "token_limit": TOKEN_LIMIT,
        "max_document_tokens": diagnostics["max_document_tokens"],
        "over_limit_paragraphs": diagnostics["over_limit_paragraphs"],
        "split_paragraphs": diagnostics["split_paragraphs"],
        "token_split_fragments": diagnostics["token_split_fragments"],
        "heading_prefix_overflow_fallbacks": diagnostics[
            "heading_prefix_overflow_fallbacks"
        ],
        "tokenizer": f"{KURE_MODEL_ID}@{KURE_REVISION}",
        "heading_heuristic": (
            "Matched canonical layout block dominated (>=60% normalized characters) "
            "by bold text >=12pt and >=1.25x the page's median regular font size."
        ),
    }


def _is_page_footer(block: str, *, page_number: int, page_count: int) -> bool:
    match = re.fullmatch(
        r"\s*(?:(?:page|p\.?|쪽|페이지)\s*)?(\d{1,5})"
        r"\s*(?:/|of|중)\s*(\d{1,5})\s*",
        block,
        flags=re.IGNORECASE,
    )
    return bool(
        match
        and int(match.group(1)) == page_number
        and int(match.group(2)) == page_count
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for data in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Advanced 원본 PDF를 폰트 기반 소제목+문단 청크로 준비합니다."
    )
    parser.parse_args()
    if DATASET != "advanced":
        raise SystemExit("이 캐시 경로는 Advanced PDF 데이터만 지원합니다.")

    chunks, diagnostics = build_chunks()
    CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=CHUNKS_PATH.parent) as temporary:
        temporary_path = Path(temporary) / CHUNKS_PATH.name
        temporary_path.write_text(
            json.dumps(chunks, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary_path.replace(CHUNKS_PATH)

    diagnostics["chunk_sha256"] = _sha256(CHUNKS_PATH)
    diagnostics["source_page_cache"] = str(CACHE_PATH.relative_to(PROJECT_ROOT))
    diagnostics["source_page_cache_sha256"] = _sha256(CACHE_PATH)
    with TemporaryDirectory(dir=METADATA_PATH.parent) as temporary:
        temporary_path = Path(temporary) / METADATA_PATH.name
        temporary_path.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(METADATA_PATH)

    diagnostics["output"] = str(CHUNKS_PATH.relative_to(PROJECT_ROOT))
    diagnostics["metadata"] = str(METADATA_PATH.relative_to(PROJECT_ROOT))
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
