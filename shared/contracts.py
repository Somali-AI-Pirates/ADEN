"""모듈 공통 입출력 타입 (docs/design/architecture.md 4장).

이 파일(shared/)의 담당은 B다. 지금 있는 타입은 A의 검색 PR에 필요한 세 개뿐인 초안이며,
B의 공통 계약 PR이 먼저 병합되면 그쪽 정의를 따른다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PageDocument:
    book_id: str  # PDF 파일명에서 .pdf만 뺀 값 (예: SB01)
    file_name: str
    page: int  # PDF 첫 장이 1
    text: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str  # "{book_id}-p{page}-c{순번}" (예: SB01-p1-c1)
    book_id: str
    file_name: str
    page: int  # 청크는 페이지를 넘지 않으므로 출처 페이지는 하나
    text: str  # 본문만. 모델 문맥에는 이 값을 넣는다
    heading: str = ""  # "[SB01 | 부서] 문서 제목 > 소제목". 색인 입력에만 앞에 붙인다


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    rank: int  # 1부터
    score: float  # 방식마다 척도가 다르므로 방식끼리 비교하지 않는다
    retriever: str  # "dense" 등 점수를 낸 단계
