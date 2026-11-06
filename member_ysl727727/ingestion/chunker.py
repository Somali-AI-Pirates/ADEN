"""페이지 안에서 문단 경계로 자르는 청킹.

Basic 해운 페이지 구조:
    1줄  "SB02 | 예약지원팀·반입운영팀"   문서 ID | 담당 부서
    2줄  "예약 확정 후 준비와 장비 인수"    문서 제목
    3줄  "확정 확인서의 점검"              페이지 소제목
    본문 (PDF 줄바꿈으로 잘린 문단들)
    끝줄 "1 / 24"                         페이지 번호 (버림)

문단 800자를 고른 이유 (근거 인용 258개, E5 토큰 기준으로 비교):
    페이지 통째   인용 100% 보존, 하지만 360개 중 106개가 E5 한도 512토큰을 넘어 뒷부분이 잘림
    문단 800자    인용 98.1% 보존, 최대 482토큰으로 한도 안
    글자 500/100  인용 84.1% 보존 (문장 중간에서 잘림)
"""
import re

from shared.contracts import Chunk

FOOTER = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
SENTENCE_END = re.compile(r"(다|요|음|함)\.$|[.!?]\"?$")


def _parse_page(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and FOOTER.match(lines[-1]):
        lines = lines[:-1]
    header, title, section = (lines + ["", "", ""])[:3]
    return header, title, section, lines[3:]


def _join(prev, line):
    # PDF 줄바꿈은 단어 중간(약 60%)과 띄어쓰기 자리(약 40%) 둘 다에서 생긴다.
    # 문장부호 뒤만 띄우고 나머지는 붙인다. 어느 규칙이든 띄어쓰기 오류가 약 3% 남는다.
    if not prev:
        return line
    return prev + (" " if prev.endswith((".", ",", ")", "?", "!", ":", "”", "’")) else "") + line


def _paragraphs(body_lines):
    paras, cur = [], ""
    for line in body_lines:
        cur = _join(cur, line)
        if SENTENCE_END.search(line):
            paras.append(cur.strip())
            cur = ""
    if cur.strip():
        paras.append(cur.strip())
    return paras


def _pack(paras, max_chars):
    # 문단을 max_chars까지 채운다. 한 문단이 max_chars보다 길면 자르지 않고 그대로 둔다.
    chunks, cur = [], []
    for p in paras:
        if cur and len(" ".join(cur + [p])) > max_chars:
            chunks.append(" ".join(cur))
            cur = []
        cur.append(p)
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def chunk_pages(pages, max_chars=800):
    """PageDocument 목록 → Chunk 목록. 청크는 페이지 경계를 넘지 않는다."""
    chunks = []
    for page in pages:
        header, title, section, body = _parse_page(page.text)
        heading = f"[{header}] {title} > {section}"
        for i, text in enumerate(_pack(_paragraphs(body), max_chars), start=1):
            chunks.append(Chunk(
                chunk_id=f"{page.book_id}-p{page.page}-c{i}",
                book_id=page.book_id, file_name=page.file_name, page=page.page,
                text=text, heading=heading,
            ))
    return chunks


def index_text(chunk):
    """Dense·BM25 색인에 넣는 텍스트. 제목·소제목을 붙여 짧은 청크도 주제를 알 수 있게 한다."""
    return f"{chunk.heading}\n{chunk.text}" if chunk.heading else chunk.text
