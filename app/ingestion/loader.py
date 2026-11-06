"""PDF 페이지별 텍스트 추출."""
from pathlib import Path

from pypdf import PdfReader

from shared.contracts import PageDocument


def extract_pages(pdf_paths, *, extraction_mode="plain"):
    """PDF 목록 → (PageDocument 목록, 처리 내역).

    한 파일이 실패해도 멈추지 않고 실패 목록에 남긴 뒤 다음 파일로 넘어간다.
    """
    pages = []
    report = {"files": [], "empty_pages": [], "failed": []}
    for path in map(Path, pdf_paths):
        try:
            texts = [p.extract_text(extraction_mode=extraction_mode) or "" for p in PdfReader(path).pages]
        except Exception as e:
            report["failed"].append({"file_name": path.name, "error": repr(e)[:200]})
            continue
        report["files"].append({"file_name": path.name, "pages": len(texts)})
        for i, text in enumerate(texts, start=1):
            if not text.strip():
                report["empty_pages"].append(f"{path.stem} p{i}")
            pages.append(PageDocument(book_id=path.stem, file_name=path.name, page=i, text=text))
    return pages, report
