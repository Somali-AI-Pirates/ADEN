"""PDF → 페이지 텍스트 캐시 (단계별). 평가 정답 파일은 읽지 않는다.

    <gpu python> experimental/ysl727727/overnight/prepare.py basic inter adv
출력: artifacts/overnight/<level>/pages.jsonl, extract_report.json
"""
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from common import LEVELS, OUT, log, write_jsonl


def extract(path):
    from pypdf import PdfReader

    path = Path(path)
    try:
        texts = [p.extract_text() or "" for p in PdfReader(path).pages]
    except Exception as e:  # noqa: BLE001
        return path.name, None, repr(e)[:200]
    return path.name, texts, None


def main(levels):
    for level in levels:
        t = time.time()
        pdfs = sorted(p for d in LEVELS[level]["docs"] for p in d.glob("*.pdf"))
        pages, report = [], {"files": [], "failed": [], "empty_pages": []}
        with ProcessPoolExecutor(12) as ex:
            for name, texts, err in ex.map(extract, pdfs):
                if err:
                    report["failed"].append({"file": name, "error": err})
                    continue
                report["files"].append({"file": name, "pages": len(texts)})
                for i, tx in enumerate(texts, start=1):
                    if not tx.strip():
                        report["empty_pages"].append(f"{name} p{i}")
                    pages.append({"book_id": Path(name).stem, "file_name": name, "page": i, "text": tx})
        out = OUT / level
        write_jsonl(out / "pages.jsonl", pages)
        (out / "extract_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"{level}: PDF {len(pdfs)}개, 페이지 {len(pages)}개, 빈 페이지 {len(report['empty_pages'])}, "
            f"실패 {len(report['failed'])}, {time.time() - t:.0f}s")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:] or ["basic", "inter", "adv"])
