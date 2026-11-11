"""Extract original PDFs into a new directory; no prior artifacts are inputs."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import subprocess
import time
from pathlib import Path


def extract_corpus(documents: Path, output: Path, workers: int = 6):
    if (output / "pages.jsonl").exists():
        raise FileExistsError("Use a fresh directory; original extraction is immutable")
    (output / "raw").mkdir(parents=True, exist_ok=True)
    paths = sorted(documents.glob("*.pdf"))
    if not paths:
        raise ValueError("No original PDFs found")

    def one(pdf):
        dest = output / "raw" / f"{pdf.stem}.txt"
        subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), str(dest)], check=True
        )
        texts = dest.read_text().split("\f")
        if not texts[-1].strip():
            texts.pop()
        metadata = {
            "book_id": pdf.stem,
            "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "pages": len(texts),
        }
        pages = [
            {
                "source_id": f"{pdf.stem}:{n:03d}",
                "book_id": pdf.stem,
                "page": n,
                "text": t,
                "lines": t.splitlines(),
            }
            for n, t in enumerate(texts, 1)
        ]
        return metadata, pages

    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        books = list(pool.map(one, paths))
    with (output / "pages.jsonl").open("w") as file:
        for _, rows in books:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {
        "files": [book[0] for book in books],
        "pages": sum(len(b[1]) for b in books),
        "seconds": time.perf_counter() - start,
    }
    (output / "raw-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--documents", type=Path, default=Path("data/advanced/documents")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = extract_corpus(args.documents, args.output)
    print(json.dumps({"books": len(result["files"]), "pages": result["pages"]}))


if __name__ == "__main__":
    main()
