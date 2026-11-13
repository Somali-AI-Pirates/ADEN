"""Add source-linked IS paragraph vectors without deleting original page units."""

import argparse
import re
import time
from pathlib import Path
import numpy as np
from .common import BUNDLE, OUT, REC, read, write, file_sha, activate


def extract(pages):
    units = []
    for page in pages:
        text = page["text"]
        if not re.match(r"IS\d+\s*\|", text):
            continue
        source = f"{Path(page['file_name']).stem}:p{page['page']:04d}"
        blocks = list(re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text))
        heading = "\n\n".join(m.group() for m in blocks[:3])
        for n, m in enumerate(blocks[3:], 3):
            body = m.group().strip()
            # Narrative support only: no flattened rows, no automatic policy merging.
            if (
                len(body) < 65
                or re.match(r"(?:IS\d+-\d+-\d+|\d+\s*/\s*\d+)", body)
                or not re.search(r"[.!?。]|다[.\s]|니다", body)
            ):
                continue
            if len(body) > 1800:
                continue
            units.append(
                {
                    "unit_id": source + f":paragraph-{n}",
                    "source_id": source,
                    "file_name": page["file_name"],
                    "page": page["page"],
                    "book_id": Path(page["file_name"]).stem,
                    "start": m.start(),
                    "end": m.end(),
                    "raw_text": m.group(),
                    "text": heading + "\n\n" + body,
                }
            )
    return units


def build():
    activate()
    import torch
    import torch.nn.functional as functional
    from sentence_transformers import SentenceTransformer

    p = OUT / "paragraph-index"
    p.mkdir(parents=True, exist_ok=True)
    pages = read(BUNDLE / "pages.json")
    units = extract(pages)
    manifest = read(BUNDLE / "corpus.json")
    embedding = manifest["embedding"]
    spec = {
        "pages_sha256": file_sha(BUNDLE / "pages.json"),
        "extract_code_sha256": file_sha(Path(__file__)),
        "embedding": embedding,
        "count": len(units),
        "max_length": 2048,
        "dtype": "bfloat16",
        "batch_size": 2,
    }
    if (p / "manifest.json").exists():
        old = read(p / "manifest.json")
        if old["spec"] != spec:
            raise RuntimeError("Paragraph build inputs changed")
        return
    write(p / "units.json", units)
    pages_by_source = {
        f"{Path(x['file_name']).stem}:p{x['page']:04d}": x["text"] for x in pages
    }
    assert all(
        pages_by_source[u["source_id"]][u["start"] : u["end"]] == u["raw_text"]
        for u in units
    )
    model = SentenceTransformer(
        embedding["model_id"],
        revision=embedding["revision"],
        local_files_only=True,
        device="cuda",
        model_kwargs={"dtype": torch.bfloat16},
    )
    model.max_seq_length = 2048
    assert model.prompts.get("document") == embedding["document_prefix"]
    started = time.perf_counter()
    vectors = np.lib.format.open_memmap(
        p / "vectors.partial.npy",
        mode="w+",
        dtype=np.float32,
        shape=(len(units), embedding["dimensions"]),
    )
    for begin in range(0, len(units), 32):
        with torch.inference_mode():
            batch = model.encode_document(
                [u["text"] for u in units[begin : begin + 32]],
                batch_size=2,
                show_progress_bar=False,
                normalize_embeddings=False,
                convert_to_tensor=True,
            )
            batch = functional.normalize(batch.float(), p=2, dim=1).cpu().numpy()
        vectors[begin : begin + len(batch)] = batch
        if begin % 512 == 0:
            print(f"Paragraph vectors {begin}/{len(units)}", flush=True)
    vectors.flush()
    del vectors
    (p / "vectors.partial.npy").replace(p / "vectors.npy")
    result = {
        "spec": spec,
        "units_sha256": file_sha(p / "units.json"),
        "vectors_sha256": file_sha(p / "vectors.npy"),
        "seconds": time.perf_counter() - started,
        "source_spans_verified": len(units),
        "deduplicated_rules": False,
        "inferred_scope": False,
    }
    write(p / "manifest.json", result)
    write(REC / "paragraph-index.json", result)
    print("Paragraph index complete", len(units), flush=True)


def augmented(base):
    from langchain_core.documents import Document
    from app.rag.corpus import TableRagCorpus

    p = OUT / "paragraph-index"
    manifest = read(p / "manifest.json")
    if (
        file_sha(p / "units.json") != manifest["units_sha256"]
        or file_sha(p / "vectors.npy") != manifest["vectors_sha256"]
    ):
        raise RuntimeError("Paragraph index fingerprint mismatch")
    units = read(p / "units.json")
    docs = tuple(
        Document(
            u["text"],
            metadata={
                k: u[k]
                for k in ["unit_id", "source_id", "book_id", "file_name", "page"]
            },
        )
        for u in units
    )
    vectors = np.concatenate([base.vectors, np.load(p / "vectors.npy", mmap_mode="r")])
    return TableRagCorpus(
        base.documents + docs,
        base.pages_by_source,
        vectors,
        base.table_store,
        base.embedding,
        base.page_count,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build"])
    parser.parse_args()
    build()
