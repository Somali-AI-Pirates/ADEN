import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from experimental.u_10o0o.embeding import BATCH_SIZE, VECTORS_PATH, load_model
from experimental.u_10o0o.prepare import CACHE_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Snowflake-ko 문서 벡터 생성")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()

    if not CACHE_PATH.is_file():
        raise SystemExit(
            "전처리 결과를 먼저 만드세요. uv run python -m experimental.u_10o0o.prepare"
        )

    pages = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    if not pages:
        raise SystemExit("임베딩할 페이지가 없습니다.")

    model = load_model(args.device)
    token_limit = model.max_seq_length
    if token_limit is None:
        raise ValueError("Snowflake-ko 모델의 입력 토큰 한도를 확인할 수 없습니다.")
    texts = [page["text"] for page in pages]
    input_ids = model.tokenizer(texts, truncation=False, verbose=False)["input_ids"]
    lengths = [len(ids) for ids in input_ids]
    over_limit = [
        (page, length)
        for page, length in zip(pages, lengths, strict=True)
        if length > token_limit
    ]
    print(f"전체 {len(pages)}쪽 | 입력 한도 {token_limit}토큰")
    print(f"최대 {max(lengths)}토큰 | 한도 초과 {len(over_limit)}쪽")
    for page, length in over_limit[:3]:
        print(f"{page['file_name']} {page['page']}쪽 | {length}토큰")

    document_vectors = model.encode_document(
        texts, batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=True
    )
    with TemporaryDirectory(dir=VECTORS_PATH.parent) as temporary:
        staged_path = Path(temporary) / VECTORS_PATH.name
        np.save(staged_path, document_vectors, allow_pickle=False)
        staged_path.replace(VECTORS_PATH)

    print(f"{document_vectors.shape} 저장 → {VECTORS_PATH}")


if __name__ == "__main__":
    main()
