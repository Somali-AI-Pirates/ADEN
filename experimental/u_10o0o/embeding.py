from functools import cache

from experimental.u_10o0o.prepare import CACHE_PATH, DATASET

MODEL_ID = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
MODEL_REVISION = "55ec6e9358a56d56af759bc8372e970caf8c305f"
VECTORS_PATH = CACHE_PATH.with_name(f"{DATASET}_snowflake_ko_vectors.npy")
EMBEDDING_DIM = 1024
CPU_THREADS = 8
BATCH_SIZE = 8


@cache
def load_model(device="cpu"):
    import torch
    from sentence_transformers import SentenceTransformer

    if device == "cpu":
        torch.set_num_threads(CPU_THREADS)
    elif device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("PyTorch에서 CUDA를 사용할 수 없습니다.")
    return SentenceTransformer(
        MODEL_ID,
        revision=MODEL_REVISION,
        device=device,
        model_kwargs={"dtype": torch.float32},
    )
