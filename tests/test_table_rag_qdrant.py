import json

import numpy as np
import pytest
from langchain_core.documents import Document
from qdrant_client import QdrantClient, models

from app.rag import qdrant_index
from app.rag.corpus import TableRagCorpus


@pytest.fixture
def qdrant_client():
    client = QdrantClient(location=":memory:")
    yield client
    client.close()


@pytest.fixture
def corpus_bundle(tmp_path, monkeypatch):
    embedding = {
        "model_id": "nlpai-lab/KURE-v3",
        "revision": "73d5e710bc0b1f324a60746922cbad6d38f480d4",
        "dimensions": 2048,
        "query_max_length": 2048,
        "page_max_length": 8192,
        "table_max_length": 2048,
        "query_prefix": "query: ",
        "document_prefix": "passage: ",
        "normalized": True,
        "model_dtype": "bfloat16",
        "output_dtype": "float32",
        "encoder_sha256": "c" * 64,
        "encoder_dependencies": {"sentence-transformers": "5.2.0"},
        "build_device": "cpu",
    }
    vectors = np.zeros((3, 2048), dtype=np.float32)
    vectors[0, 0] = 1.0
    vectors[1, 0] = 1.0
    vectors[2, 1] = 1.0
    documents = (
        Document(
            "Page one text",
            metadata={
                "unit_id": "AS001:p0001:page",
                "source_id": "AS001:p0001",
                "book_id": "AS001",
                "file_name": "AS001.pdf",
                "page": 1,
            },
        ),
        Document(
            "Table one text",
            metadata={
                "unit_id": "AS001:p0001:t001",
                "source_id": "AS001:p0001",
                "book_id": "AS001",
                "file_name": "AS001.pdf",
                "page": 1,
                "table_id": "AS001:p0001:t001",
            },
        ),
        Document(
            "Page two text",
            metadata={
                "unit_id": "AS001:p0002:page",
                "source_id": "AS001:p0002",
                "book_id": "AS001",
                "file_name": "AS001.pdf",
                "page": 2,
            },
        ),
    )
    corpus = TableRagCorpus(
        documents=documents,
        pages_by_source={
            document.metadata["source_id"]: document
            for document in (documents[0], documents[2])
        },
        vectors=vectors,
        table_store=None,
        embedding=embedding,
        page_count=2,
    )
    manifest_path = tmp_path / "corpus.json"
    manifest = {
        "schema_version": 1,
        "dataset": "advanced",
        "embedding": embedding,
        "counts": {"pages": 2, "table_documents": 1, "physical_tables": 1},
        "files": {"vectors.npy": "a" * 64},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(qdrant_index, "_load_corpus", lambda _directory: corpus)
    return tmp_path, manifest_path, manifest, corpus


def test_upload_is_resumable_verified_and_stores_no_text(corpus_bundle, qdrant_client):
    directory, _manifest_path, _manifest, _corpus = corpus_bundle

    first = qdrant_index.upload_qdrant_index(
        directory, client=qdrant_client
    )
    assert first["status"] == "ready"
    assert first["points"] == 3
    assert qdrant_client.count(qdrant_index.DEFAULT_COLLECTION, exact=True).count == 3

    records, _ = qdrant_client.scroll(
        qdrant_index.DEFAULT_COLLECTION,
        limit=10,
        with_payload=True,
        with_vectors=True,
    )
    assert {record.id for record in records} == {0, 1, 2}
    assert all("text" not in (record.payload or {}) for record in records)

    info = qdrant_client.get_collection(qdrant_index.DEFAULT_COLLECTION)
    building_metadata = dict(info.config.metadata)
    building_metadata["aden_status"] = "building"
    qdrant_client.update_collection(
        qdrant_index.DEFAULT_COLLECTION,
        metadata=building_metadata,
    )
    qdrant_client.delete(
        qdrant_index.DEFAULT_COLLECTION,
        points_selector=models.PointIdsList(points=[1, 2]),
        wait=True,
    )
    resumed = qdrant_index.upload_qdrant_index(
        directory, client=qdrant_client
    )
    assert resumed["action"] == "resumed"
    assert qdrant_client.count(qdrant_index.DEFAULT_COLLECTION, exact=True).count == 3

    verified = qdrant_index.upload_qdrant_index(
        directory, client=qdrant_client
    )
    assert verified["action"] == "verified"
    assert qdrant_index.check_qdrant_index(
        directory, client=qdrant_client, full=True
    )["validation"] == "vectors-and-payload"


def test_exact_search_expands_ties_and_uses_corpus_row_order(corpus_bundle, qdrant_client):
    directory, _manifest_path, _manifest, corpus = corpus_bundle
    qdrant_index.upload_qdrant_index(directory, client=qdrant_client)

    index = qdrant_index.open_qdrant_index(
        directory, corpus, client=qdrant_client
    )
    ranked = index.search(corpus.vectors[0], top_k=1)
    assert ranked[0][0].metadata["unit_id"] == "AS001:p0001:page"
    assert ranked[0][1] == pytest.approx(1.0)
    assert index.manifest["search_mode"] == "exact"
    index.close()
    index.close()
    with pytest.raises(RuntimeError, match="closed"):
        index.search(corpus.vectors[0], top_k=1)


def test_upload_refuses_changed_fingerprint_without_mutating_ready_collection(
    corpus_bundle, qdrant_client
):
    directory, manifest_path, manifest, _corpus = corpus_bundle
    qdrant_index.upload_qdrant_index(directory, client=qdrant_client)
    original = qdrant_client.get_collection(qdrant_index.DEFAULT_COLLECTION)
    changed = {**manifest, "files": {"vectors.npy": "b" * 64}}
    manifest_path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(qdrant_index.QdrantIndexError, match="different corpus"):
        qdrant_index.upload_qdrant_index(directory, client=qdrant_client)

    current = qdrant_client.get_collection(qdrant_index.DEFAULT_COLLECTION)
    assert current.config.metadata == original.config.metadata
    assert qdrant_client.count(qdrant_index.DEFAULT_COLLECTION, exact=True).count == 3


def test_search_rejects_a_point_with_wrong_unit_mapping(corpus_bundle, qdrant_client):
    directory, _manifest_path, _manifest, corpus = corpus_bundle
    qdrant_index.upload_qdrant_index(directory, client=qdrant_client)
    qdrant_client.upsert(
        qdrant_index.DEFAULT_COLLECTION,
        points=[
            models.PointStruct(
                id=0,
                vector=corpus.vectors[0].tolist(),
                payload={
                    **qdrant_index._point_payload(corpus.documents[0], 0),
                    "unit_id": "wrong-unit",
                },
            )
        ],
        wait=True,
    )
    index = qdrant_index.open_qdrant_index(
        directory, corpus, client=qdrant_client
    )

    with pytest.raises(qdrant_index.QdrantIndexError, match="unit/source mapping"):
        index.search(corpus.vectors[0], top_k=1)


def test_collection_name_guard_keeps_basic_collection_untouched(corpus_bundle, qdrant_client):
    directory, _manifest_path, _manifest, _corpus = corpus_bundle
    qdrant_client.create_collection(
        "basic_multilingual-e5-small_p800",
        vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
    )
    qdrant_client.upsert(
        "basic_multilingual-e5-small_p800",
        points=[models.PointStruct(id=0, vector=[1.0] + [0.0] * 383, payload={"chunk_id": "keep"})],
        wait=True,
    )

    with pytest.raises(ValueError, match="advanced_table_rag"):
        qdrant_index.upload_qdrant_index(
            directory,
            collection="basic_multilingual-e5-small_p800",
            client=qdrant_client,
        )

    assert qdrant_client.count("basic_multilingual-e5-small_p800", exact=True).count == 1


@pytest.mark.parametrize(
    "url",
    ["http://example.invalid:6333", "", "https://user:password@example.invalid",
     "https://example.invalid?api_key=secret"],
)
def test_cloud_connection_requires_https_before_sending_credentials(monkeypatch, url):
    from app.indexing import vector_store
    from app.rag.qdrant_index import QdrantIndexError, _connect

    monkeypatch.setenv("QDRANT_URL", url)
    monkeypatch.setenv("QDRANT_API_KEY", "secret-api-key")
    calls = []
    monkeypatch.setattr(vector_store, "connect", lambda: calls.append(True))
    with pytest.raises(QdrantIndexError, match="HTTPS QDRANT_URL") as error:
        _connect(None)
    assert calls == []
    assert "secret-api-key" not in str(error.value)
    if url:
        assert url not in str(error.value)


@pytest.mark.parametrize("datatype", [models.Datatype.FLOAT16, models.Datatype.UINT8])
def test_collection_rejects_reduced_precision_vector_storage(datatype):
    from types import SimpleNamespace
    from app.rag.qdrant_index import QdrantIndexError, _validate_vector_config

    info = SimpleNamespace(config=SimpleNamespace(
        params=SimpleNamespace(vectors=models.VectorParams(
            size=2048, distance=models.Distance.COSINE, datatype=datatype,
        )),
        quantization_config=None,
    ))
    with pytest.raises(QdrantIndexError, match="float32 vector storage"):
        _validate_vector_config(info)
