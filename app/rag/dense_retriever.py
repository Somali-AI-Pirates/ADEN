from typing import Callable

import numpy as np
from langchain_core.retrievers import BaseRetriever

from app.rag.corpus import TableRagCorpus
from app.rag.retrieval import rank_dense


class DenseRetriever(BaseRetriever):
    corpus: TableRagCorpus
    embed_query: Callable[[str], np.ndarray]
    top_k: int = 5

    def _get_relevant_documents(self, query, *, run_manager):
        vector = self.embed_query(query)
        ranked = rank_dense(self.corpus, vector, top_k=self.top_k)
        return [doc for doc, _ in ranked]
