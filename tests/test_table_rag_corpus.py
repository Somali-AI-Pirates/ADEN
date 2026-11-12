from types import SimpleNamespace

from app.rag.corpus import TableRagCorpus


class FakeMap:
    def __init__(self):
        self.closed = False
        self.close_count = 0

    def close(self):
        self.close_count += 1
        self.closed = True


def test_corpus_close_releases_only_its_mmap_once():
    memory_map = FakeMap()
    corpus = TableRagCorpus(
        documents=(),
        pages_by_source={},
        vectors=SimpleNamespace(_mmap=memory_map),
        table_store=None,
        embedding={},
        page_count=0,
    )

    corpus.close()
    corpus.close()

    assert memory_map.closed is True
    assert memory_map.close_count == 1


def test_corpus_close_is_noop_for_in_memory_vectors():
    import numpy as np

    corpus = TableRagCorpus(
        documents=(),
        pages_by_source={},
        vectors=np.zeros((0, 0), dtype=np.float32),
        table_store=None,
        embedding={},
        page_count=0,
    )

    corpus.close()
