"""사전 데이터 처리: PDF → 페이지 → 청크 → Dense 색인.

    python -m scripts.build_index                # 저장소 루트에서 실행

입력: data/basic/documents/*.pdf 만 읽는다 (평가 JSON은 읽지 않음)
출력: artifacts/basic/pages.jsonl, chunks.jsonl, ingestion_report.json, index_manifest.json
      storage/qdrant/ (Dense 색인)
"""
import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

from app.indexing.embeddings import DEFAULT_MODEL, Embedder
from app.indexing.vector_store import COLLECTION, build_vector_index
from app.ingestion.chunker import chunk_pages, index_text
from app.ingestion.loader import extract_pages
from app.retrieval.service import retrieve

sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 출력


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/basic")
    ap.add_argument("--artifacts", default="artifacts/basic")
    ap.add_argument("--storage", default="storage/qdrant")
    ap.add_argument("--max-chars", type=int, default=800)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    out = Path(args.artifacts)
    out.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(Path(args.data, "documents").glob("*.pdf"))
    assert pdfs, f"{args.data}/documents 에 PDF가 없습니다. data/README.md 를 보고 자료를 배치하세요."

    t = time.time()
    pages, report = extract_pages(pdfs)
    chunks = chunk_pages(pages, args.max_chars)
    write_jsonl(out / "pages.jsonl", pages)
    write_jsonl(out / "chunks.jsonl", chunks)
    per_file = {}
    for c in chunks:
        per_file[c.file_name] = per_file.get(c.file_name, 0) + 1
    for f in report["files"]:
        f["chunks"] = per_file.get(f["file_name"], 0)
    report.update(pdf_count=len(pdfs), page_count=len(pages), chunk_count=len(chunks))
    (out / "ingestion_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"추출·청킹 {time.time() - t:.1f}s | PDF {len(pdfs)}개, 페이지 {len(pages)}개, 청크 {len(chunks)}개")
    print(f"  빈 페이지 {len(report['empty_pages'])}개, 실패 {len(report['failed'])}개 {report['failed']}")

    assert len({c.chunk_id for c in chunks}) == len(chunks), "chunk_id 중복"

    t = time.time()
    embedder = Embedder(args.model)
    tokens = [embedder.passage_tokens(index_text(c)) for c in chunks]
    over = sum(n > embedder.max_tokens for n in tokens)
    print(f"임베딩 입력 토큰 최대 {max(tokens)} / 한도 {embedder.max_tokens} | 초과 {over}개 (초과분은 잘려서 임베딩됨)")

    client = build_vector_index(chunks, embedder, args.storage)
    assert client.count(COLLECTION).count == len(chunks), "색인 개수와 청크 개수가 다름"
    print(f"Dense 색인 {time.time() - t:.1f}s → {args.storage}")

    # 자체 점검: 청크 본문으로 검색하면 그 청크가 1등이어야 한다 (정답 파일 없이 확인)
    res = SimpleNamespace(embedder=embedder, qdrant=client)
    for c in chunks[:: max(1, len(chunks) // 20)]:
        top = retrieve(c.text, "naive", res, top_n=1)[0].chunk
        assert top.chunk_id == c.chunk_id, (c.chunk_id, top.chunk_id)
    print("자체 점검 OK: 청크 본문으로 검색하면 자기 청크가 1위")
    client.close()

    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "documents": [p.name for p in pdfs],
        "chunking": {"strategy": "paragraph", "max_chars": args.max_chars},
        "chunk_count": len(chunks),
        "chunks_sha256": hashlib.sha256((out / "chunks.jsonl").read_bytes()).hexdigest(),
        "embedding": {"model": args.model, "dim": embedder.dim, "max_tokens": embedder.max_tokens,
                      "prefix": {"query": "query: ", "passage": "passage: "}, "normalized": True,
                      "sentence_transformers": version("sentence-transformers")},
        "vector_store": {"engine": "qdrant-local", "path": args.storage, "collection": COLLECTION,
                         "distance": "cosine", "qdrant_client": version("qdrant-client")},
    }
    (out / "index_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료 → {out}/")


if __name__ == "__main__":
    main()
