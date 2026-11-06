"""문서·질문 임베딩. 문서와 질문이 같은 모델·같은 규칙을 쓰도록 한 곳에 둔다."""
DEFAULT_MODEL = "intfloat/multilingual-e5-small"  # 384차원, 최대 512토큰


class Embedder:
    """E5는 질문에 'query: ', 문서에 'passage: ' 접두어를 붙여야 제 성능이 나온다."""

    def __init__(self, model_id=DEFAULT_MODEL, device=None):
        from sentence_transformers import SentenceTransformer  # import 시 모델을 읽지 않도록 안에서

        self.model_id = model_id
        self.model = SentenceTransformer(model_id, device=device)
        self.dim = self.model.get_embedding_dimension()
        self.max_tokens = self.model.max_seq_length

    def embed_passages(self, texts, batch_size=32):
        return self.model.encode([f"passage: {t}" for t in texts],
                                 batch_size=batch_size, normalize_embeddings=True)

    def embed_query(self, text):
        return self.model.encode(f"query: {text}", normalize_embeddings=True)

    def passage_tokens(self, text):
        return len(self.model.tokenizer(f"passage: {text}")["input_ids"])
