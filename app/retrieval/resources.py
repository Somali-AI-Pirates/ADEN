"""한 실행 자원이 소유하는 지연 초기화 검색 자원."""

from _thread import LockType
from dataclasses import dataclass, field
from threading import Lock

from qdrant_client import QdrantClient

from app.indexing.keyword_store import KeywordIndex, load_keyword_index
from app.retrieval.reranker import Reranker


@dataclass
class RetrievalResources:
    keyword_index: KeywordIndex | None = None
    reranker: Reranker | None = None
    _lock: LockType = field(default_factory=Lock, init=False, repr=False)

    def keyword_index_for(self, client: QdrantClient, collection: str) -> KeywordIndex:
        with self._lock:
            if self.keyword_index is None:
                self.keyword_index = load_keyword_index(client, collection)
            return self.keyword_index

    def reranker_for(self) -> Reranker:
        with self._lock:
            if self.reranker is None:
                self.reranker = Reranker()
            return self.reranker

    def clear(self) -> None:
        with self._lock:
            self.keyword_index = None
            self.reranker = None
