"""청크 변형 + 임베딩 캐시 (로컬 GPU, 정답 파일 사용 안 함).

청크 변형
    p800   main chunk_pages(800) 그대로 (팀 Basic 색인과 같은 620개 확인)
    page   페이지 통째 1청크 (머리 3줄은 heading)
    p400   main chunk_pages(400)
임베딩 캐시: artifacts/overnight/<level>/<variant>/emb_<model>.npy (float16, 정규화)
"""
import json
import time

import numpy as np

from common import OUT, log, read_jsonl, write_jsonl

MODELS = {
    # id: (hf id, query prefix, passage prefix, max_seq)
    "e5s": ("intfloat/multilingual-e5-small", "query: ", "passage: ", 512),
    "e5b": ("intfloat/multilingual-e5-base", "query: ", "passage: ", 512),
    "e5l": ("intfloat/multilingual-e5-large-instruct",
            "Instruct: Given a question about shipping business manuals, retrieve passages that answer it\nQuery: ",
            "", 512),
    "m3": ("BAAI/bge-m3", "", "", 1024),
    "kure": ("nlpai-lab/KURE-v1", "", "", 1024),
    "qwen06": ("Qwen/Qwen3-Embedding-0.6B",
               "Instruct: Given a question about shipping business manuals, retrieve passages that answer it\nQuery:",
               "", 1024),
}


def chunks(level, variant="p800"):
    path = OUT / level / variant / "chunks.jsonl"
    if path.exists():
        return read_jsonl(path)
    if variant.startswith("c"):
        # 문서 카드 접두: c800 = p800 청크 + "[문서 개요] 카드" (cards.py가 만든 문서별 LLM 카드)
        import json

        from common import HERE

        cards = json.loads((HERE / "results" / f"doc_cards_{level}.json").read_text(encoding="utf-8"))
        rows = []
        for r in chunks(level, "p" + variant[1:]):
            card = cards.get(r["book_id"], "")
            rows.append(r | {"index_text": f"{r['heading']}\n[문서 개요] {card}\n{r['text']}" if card else r["index_text"]})
        write_jsonl(path, rows)
        return rows
    from app.ingestion.chunker import _pack, _paragraphs, _parse_page, chunk_pages, index_text
    from shared.contracts import PageDocument

    pages = [PageDocument(**p) for p in read_jsonl(OUT / level / "pages.jsonl")]
    rows = []
    if variant.startswith("p") and variant[1:].isdigit():
        for c in chunk_pages(pages, int(variant[1:])):
            rows.append({"chunk_id": c.chunk_id, "book_id": c.book_id, "page": c.page,
                         "heading": c.heading, "text": c.text, "index_text": index_text(c)})
    elif variant.startswith("s") and variant[1:].isdigit():
        # 긴 문단(표 행이 이어 붙은 것 등)을 공백 경계에서 max자 이하로 나눈 뒤 p800과 같이 묶는다.
        mx = int(variant[1:])

        def split_long(par):
            out = []
            while len(par) > mx:
                cut = par.rfind(" ", 0, mx)
                cut = cut if cut > mx // 2 else mx
                out.append(par[:cut].strip())
                par = par[cut:].strip()
            return out + ([par] if par else [])

        for p in pages:
            header, title, section, body = _parse_page(p.text)
            heading = f"[{header}] {title} > {section}"
            paras = [x for par in _paragraphs(body) for x in split_long(par)]
            for i, text in enumerate(_pack(paras, mx), start=1):
                rows.append({"chunk_id": f"{p.book_id}-p{p.page}-c{i}", "book_id": p.book_id, "page": p.page,
                             "heading": heading, "text": text, "index_text": f"{heading}\n{text}"})
    elif variant == "page":
        for p in pages:
            header, title, section, body = _parse_page(p.text)
            heading = f"[{header}] {title} > {section}"
            text = " ".join(_pack(_paragraphs(body), 10 ** 9))
            rows.append({"chunk_id": f"{p.book_id}-p{p.page}-c1", "book_id": p.book_id, "page": p.page,
                         "heading": heading, "text": text, "index_text": f"{heading}\n{text}"})
    else:
        raise ValueError(variant)
    write_jsonl(path, rows)
    return rows


_loaded = {}


def device():
    """OV_DEVICE=cpu 또는 cuda로 고정. 없으면 GPU가 있을 때만 cuda (팀 기본 설정은 CPU)."""
    import os

    import torch

    return os.environ.get("OV_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")


def model(mid):
    if mid not in _loaded:
        import torch
        from sentence_transformers import SentenceTransformer

        hf, _, _, max_seq = MODELS[mid]
        dev = device()
        kw = {"model_kwargs": {"torch_dtype": torch.float16}} if dev == "cuda" else {}
        m = SentenceTransformer(hf, device=dev, **kw)
        m.max_seq_length = max_seq
        _loaded[mid] = m
    return _loaded[mid]


def embeddings(level, variant, mid):
    path = OUT / level / variant / f"emb_{mid}.npy"
    if path.exists():
        return np.load(path)
    rows = chunks(level, variant)
    _, _, pp, _ = MODELS[mid]
    m = model(mid)
    t = time.time()
    texts = [pp + r["index_text"] for r in rows]
    order = np.argsort([-len(x) for x in texts])  # 길이순으로 묶어 패딩 낭비 줄이기
    emb = m.encode([texts[i] for i in order], batch_size=32, normalize_embeddings=True,
                   convert_to_numpy=True, show_progress_bar=False)
    out = np.empty_like(emb)
    out[order] = emb
    out = out.astype(np.float16)
    np.save(path, out)
    log(f"  임베딩 {level}/{variant}/{mid}: {len(rows)}개 {time.time() - t:.0f}s")
    return out


def embed_queries(texts, mid):
    _, qp, _, _ = MODELS[mid]
    return model(mid).encode([qp + t for t in texts], batch_size=32, normalize_embeddings=True,
                             convert_to_numpy=True, show_progress_bar=False).astype(np.float32)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    level, variant, mid = sys.argv[1:4]
    rows = chunks(level, variant)
    log(f"{level}/{variant}: 청크 {len(rows)}개")
    embeddings(level, variant, mid)
    json.dumps({})
