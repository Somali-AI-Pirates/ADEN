import json
from types import SimpleNamespace

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

    first = qdrant_index.upload_qdrant_index(directory, client=qdrant_client)
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
    resumed = qdrant_index.upload_qdrant_index(directory, client=qdrant_client)
    assert resumed["action"] == "resumed"
    assert qdrant_client.count(qdrant_index.DEFAULT_COLLECTION, exact=True).count == 3

    verified = qdrant_index.upload_qdrant_index(directory, client=qdrant_client)
    assert verified["action"] == "verified"
    assert (
        qdrant_index.check_qdrant_index(directory, client=qdrant_client, full=True)[
            "validation"
        ]
        == "vectors-and-payload"
    )


def test_exact_search_expands_ties_and_uses_corpus_row_order(
    corpus_bundle, qdrant_client
):
    directory, _manifest_path, _manifest, corpus = corpus_bundle
    qdrant_index.upload_qdrant_index(directory, client=qdrant_client)

    index = qdrant_index.open_qdrant_index(directory, corpus, client=qdrant_client)
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
    index = qdrant_index.open_qdrant_index(directory, corpus, client=qdrant_client)

    with pytest.raises(qdrant_index.QdrantIndexError, match="unit/source mapping"):
        index.search(corpus.vectors[0], top_k=1)


def test_collection_name_guard_keeps_basic_collection_untouched(
    corpus_bundle, qdrant_client
):
    directory, _manifest_path, _manifest, _corpus = corpus_bundle
    qdrant_client.create_collection(
        "basic_multilingual-e5-small_p800",
        vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
    )
    qdrant_client.upsert(
        "basic_multilingual-e5-small_p800",
        points=[
            models.PointStruct(
                id=0, vector=[1.0] + [0.0] * 383, payload={"chunk_id": "keep"}
            )
        ],
        wait=True,
    )

    with pytest.raises(ValueError, match="advanced_table_rag"):
        qdrant_index.upload_qdrant_index(
            directory,
            collection="basic_multilingual-e5-small_p800",
            client=qdrant_client,
        )

    assert (
        qdrant_client.count("basic_multilingual-e5-small_p800", exact=True).count == 1
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid:6333",
        "",
        "https://user:password@example.invalid",
        "https://example.invalid?api_key=secret",
    ],
)
def test_cloud_connection_requires_https_before_sending_credentials(monkeypatch, url):
    from app.rag.config import RuntimeCredentials
    from app.rag.qdrant_index import QdrantIndexError, _connect

    monkeypatch.setenv("QDRANT_URL", "https://ambient.example.invalid")
    monkeypatch.setenv("QDRANT_API_KEY", "ambient-api-key")
    calls = []
    monkeypatch.setattr(
        qdrant_index, "QdrantClient", lambda **kwargs: calls.append(kwargs)
    )
    credentials = RuntimeCredentials(qdrant_url=url, qdrant_api_key="secret-api-key")
    with pytest.raises(QdrantIndexError, match="HTTPS QDRANT_URL") as error:
        _connect(None, credentials)
    assert calls == []
    assert "secret-api-key" not in str(error.value)
    if url:
        assert url not in str(error.value)


def test_explicit_missing_qdrant_key_does_not_fall_back_to_environment(monkeypatch):
    from app.rag.config import RuntimeCredentials
    from app.rag.qdrant_index import QdrantIndexError, _connect

    monkeypatch.setenv("QDRANT_API_KEY", "ambient-api-key")
    calls = []
    monkeypatch.setattr(
        qdrant_index, "QdrantClient", lambda **kwargs: calls.append(kwargs)
    )
    credentials = RuntimeCredentials(qdrant_url="https://qdrant.example.invalid")
    with pytest.raises(QdrantIndexError, match="QDRANT_API_KEY"):
        _connect(None, credentials)
    assert calls == []


@pytest.mark.parametrize(
    "url",
    ["http://localhost:6333", "http://127.0.0.1:6333", "http://[::1]:6333"],
)
def test_local_qdrant_uses_loopback_http_without_api_key(monkeypatch, url):
    from app.rag.config import RuntimeCredentials
    from app.rag.qdrant_index import _connect

    monkeypatch.setenv("QDRANT_API_KEY", "cloud-key-must-not-leak")
    client = object()
    calls = []
    monkeypatch.setattr(
        qdrant_index,
        "QdrantClient",
        lambda **kwargs: calls.append(kwargs) or client,
    )
    credentials = RuntimeCredentials(qdrant_url=url, qdrant_local=True)

    connected, owned = _connect(None, credentials)

    assert connected is client
    assert owned is True
    assert calls == [{"url": url, "timeout": 60}]


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://localhost:6333",
        "http://qdrant:6333",
        "http://192.168.1.5:6333",
        "http://localhost:6333?api_key=secret",
        "http://user@127.0.0.1:6333",
        "http://127.0.0.1:6333/path",
    ],
)
def test_local_qdrant_rejects_non_loopback_or_credential_urls(monkeypatch, url):
    from app.rag.config import RuntimeCredentials
    from app.rag.qdrant_index import QdrantIndexError, _connect

    calls = []
    monkeypatch.setattr(
        qdrant_index, "QdrantClient", lambda **kwargs: calls.append(kwargs)
    )
    credentials = RuntimeCredentials(qdrant_url=url, qdrant_local=True)

    with pytest.raises(QdrantIndexError, match="loopback address"):
        _connect(None, credentials)

    assert calls == []


def test_local_qdrant_rejects_an_explicit_api_key(monkeypatch):
    from app.rag.config import RuntimeCredentials
    from app.rag.qdrant_index import QdrantIndexError, _connect

    calls = []
    monkeypatch.setattr(
        qdrant_index, "QdrantClient", lambda **kwargs: calls.append(kwargs)
    )
    credentials = RuntimeCredentials(
        qdrant_url="http://localhost:6333",
        qdrant_api_key="must-not-be-used",
        qdrant_local=True,
    )

    with pytest.raises(QdrantIndexError, match="must not use") as error:
        _connect(None, credentials)

    assert calls == []
    assert "must-not-be-used" not in str(error.value)


def test_qdrant_uses_explicit_credentials(monkeypatch):
    from app.rag.config import RuntimeCredentials
    from app.rag.qdrant_index import _connect

    monkeypatch.setenv("QDRANT_URL", "https://ambient.example.invalid")
    monkeypatch.setenv("QDRANT_API_KEY", "ambient-api-key")
    client = object()
    calls = []
    monkeypatch.setattr(
        qdrant_index,
        "QdrantClient",
        lambda **kwargs: calls.append(kwargs) or client,
    )
    credentials = RuntimeCredentials(
        qdrant_url="https://explicit.example.invalid",
        qdrant_api_key="explicit-api-key",
    )
    connected, owned = _connect(None, credentials)
    assert connected is client
    assert owned is True
    assert calls == [
        {
            "url": "https://explicit.example.invalid",
            "api_key": "explicit-api-key",
            "timeout": 60,
        }
    ]


@pytest.mark.parametrize("datatype", [models.Datatype.FLOAT16, models.Datatype.UINT8])
def test_collection_rejects_reduced_precision_vector_storage(datatype):
    from types import SimpleNamespace
    from app.rag.qdrant_index import QdrantIndexError, _validate_vector_config

    info = SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=models.VectorParams(
                    size=2048,
                    distance=models.Distance.COSINE,
                    datatype=datatype,
                )
            ),
            quantization_config=None,
        )
    )
    with pytest.raises(QdrantIndexError, match="float32 vector storage"):
        _validate_vector_config(info)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("status", models.CollectionStatus.YELLOW, "not ready"),
        ("optimizer_status", "error", "optimizers"),
        ("points_count", 2, "point count"),
        ("metadata", {"aden_status": "ready"}, "metadata"),
    ],
)
def test_qdrant_readiness_rejects_incompatible_collection_state(
    corpus_bundle, qdrant_client, field, replacement, message
):
    directory, _manifest_path, _manifest, _corpus = corpus_bundle
    qdrant_index.upload_qdrant_index(directory, client=qdrant_client)
    original = qdrant_client.get_collection(qdrant_index.DEFAULT_COLLECTION)
    if field == "metadata":
        config = SimpleNamespace(
            params=original.config.params,
            quantization_config=original.config.quantization_config,
            metadata=replacement,
        )
    else:
        config = original.config
    info = SimpleNamespace(
        config=config,
        status=original.status,
        optimizer_status=original.optimizer_status,
        points_count=original.points_count,
    )
    if field != "metadata":
        setattr(info, field, replacement)

    class Client:
        def __init__(self):
            self.calls = []

        def get_collection(self, collection):
            self.calls.append(collection)
            return info

    client = Client()
    with pytest.raises(qdrant_index.QdrantIndexError, match=message):
        qdrant_index.check_qdrant_readiness(directory, _corpus, client=client)
    assert client.calls == [qdrant_index.DEFAULT_COLLECTION]


def test_qdrant_readiness_makes_one_read_only_metadata_call(
    corpus_bundle, qdrant_client
):
    directory, _manifest_path, _manifest, corpus = corpus_bundle
    qdrant_index.upload_qdrant_index(directory, client=qdrant_client)
    raw_get = qdrant_client.get_collection
    before = raw_get(qdrant_index.DEFAULT_COLLECTION)

    class ReadOnlyClient:
        def __init__(self):
            self.get_calls = []

        def get_collection(self, collection):
            self.get_calls.append(collection)
            return raw_get(collection)

        def __getattr__(self, name):
            raise AssertionError(f"Readiness must not call {name}")

    client = ReadOnlyClient()
    result = qdrant_index.check_qdrant_readiness(directory, corpus, client=client)

    assert result is None
    assert client.get_calls == [qdrant_index.DEFAULT_COLLECTION]
    after = raw_get(qdrant_index.DEFAULT_COLLECTION)
    assert after.config.metadata == before.config.metadata
    assert after.points_count == before.points_count


def test_qdrant_readiness_uses_two_second_compatibility_disabled_client_and_closes(
    corpus_bundle, qdrant_client, monkeypatch
):
    from app.rag.config import RuntimeCredentials

    directory, _manifest_path, _manifest, corpus = corpus_bundle
    qdrant_index.upload_qdrant_index(directory, client=qdrant_client)
    info = qdrant_client.get_collection(qdrant_index.DEFAULT_COLLECTION)
    calls = []
    closed = []

    class Client:
        def get_collection(self, collection):
            calls.append(collection)
            return info

        def close(self):
            closed.append(True)

    client = Client()
    constructor = []

    def create_client(**kwargs):
        constructor.append(kwargs)
        return client

    monkeypatch.setattr(qdrant_index, "QdrantClient", create_client)
    credentials = RuntimeCredentials(
        qdrant_url="https://qdrant.example.invalid",
        qdrant_api_key="api-key-secret",
    )

    qdrant_index.check_qdrant_readiness(directory, corpus, credentials=credentials)

    assert constructor == [
        {
            "url": "https://qdrant.example.invalid",
            "api_key": "api-key-secret",
            "timeout": 2,
            "check_compatibility": False,
        }
    ]
    assert calls == [qdrant_index.DEFAULT_COLLECTION]
    assert closed == [True]


def test_qdrant_readiness_hides_connection_details_and_closes_on_failure(
    corpus_bundle, monkeypatch
):
    from app.rag.config import RuntimeCredentials
    from app.rag.qdrant_index import QdrantIndexError

    directory, _manifest_path, _manifest, corpus = corpus_bundle
    closed = []

    class Client:
        def get_collection(self, collection):
            raise TimeoutError("qdrant-secret-response")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(qdrant_index, "QdrantClient", lambda **kwargs: Client())
    credentials = RuntimeCredentials(
        qdrant_url="https://private-host.example.invalid",
        qdrant_api_key="secret-api-key",
    )

    with pytest.raises(QdrantIndexError) as raised:
        qdrant_index.check_qdrant_readiness(directory, corpus, credentials=credentials)

    rendered = str(raised.value)
    assert "TimeoutError" in rendered
    assert "qdrant-secret-response" not in rendered
    assert "private-host" not in rendered
    assert "secret-api-key" not in rendered
    assert closed == [True]


@pytest.mark.parametrize("operation", ["upload", "check"])
@pytest.mark.parametrize("failure_stage", ["snapshot", "connect", "none"])
def test_index_commands_close_their_local_corpus(
    operation, failure_stage, corpus_bundle, qdrant_client, monkeypatch
):
    directory, _, _, corpus = corpus_bundle
    if operation == "check" and failure_stage == "none":
        qdrant_index.upload_qdrant_index(directory, client=qdrant_client)
    closed = []
    monkeypatch.setattr(TableRagCorpus, "close", lambda self: closed.append(self))
    command = (
        qdrant_index.upload_qdrant_index
        if operation == "upload"
        else qdrant_index.check_qdrant_index
    )

    def fail(*args, **kwargs):
        raise qdrant_index.QdrantIndexError("synthetic setup failure")

    if failure_stage != "none":
        monkeypatch.setattr(
            qdrant_index,
            "_read_snapshot" if failure_stage == "snapshot" else "_connect",
            fail,
        )
        with pytest.raises(
            qdrant_index.QdrantIndexError, match="synthetic setup failure"
        ):
            command(directory, client=qdrant_client)
    else:
        command(directory, client=qdrant_client)
    assert closed == [corpus]
    # A caller-owned client remains usable after success or failure.
    assert qdrant_client.get_collections() is not None
