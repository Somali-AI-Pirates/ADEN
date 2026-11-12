import json
from pathlib import Path

from app.ingestion.table_rag_builder import (
    build_bundle,
    build_physical_store,
    build_records_index,
    build_vectors,
    check_database,
    digest,
    embedding_contract,
    typed,
)
from app.rag.records import RecordIndex


def jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def source(source_id, book, page, heading):
    return {
        "source_id": source_id,
        "dataset": "advanced",
        "book_id": book,
        "file_name": f"{book}.pdf",
        "file_sha256": "a" * 64,
        "page": page,
        "heading": heading,
        "sections": ["정산"],
    }


def record(record_id, source_id, fields, *, cases=(), identifiers=()):
    return {
        "record_id": record_id,
        "kind": "table_row",
        "source_id": source_id,
        "line_start": 10,
        "line_end": 10,
        "section": "정산",
        "fields": fields,
        "text": "",
        "identifiers": list(identifiers),
        "case_ids": list(cases),
        "context_rule_ids": [],
        "related_rule_ids": [],
        "dates": [],
        "amounts": [],
        "states": {},
        "cost_terms": {},
        "review_status": "parsed",
    }


def test_raw_table_and_common_record_snapshot_are_aligned(tmp_path):
    curated = tmp_path / "curated"
    sources = [
        source(
            "advanced:AS057:p1",
            "AS057",
            1,
            "작업철 > HS-057 > HG2029-00660 / shipper / HL-20GP-0066 / 비용 조건 약정",
        ),
        source(
            "advanced:AS058:p1",
            "AS058",
            1,
            "현장대장 > HG2029-00660 / 해온물류 / HL-20GP-0066 / 현장 사건 수신대장",
        ),
        source(
            "advanced:AS001:p1", "AS001", 1, "지원대장 > HA28-C001 비용별 정산 확인"
        ),
        source("advanced:AS007:p1", "AS007", 1, "IS01 | IS 원문 > IS01-001-01"),
    ]
    rows = [
        record(
            "hg-row",
            sources[0]["source_id"],
            {
                "항목": "실제 양하",
                "날짜 또는 상태": "2029-09-10",
                "예약": "HG2029-00660",
                "장비": "HL-20GP-0066",
                "청구액": "USD 120.50",
            },
            cases=["HG2029-00660"],
            identifiers=["HG2029-00660", "HL-20GP-0066", "CT-C-28001"],
        ),
        {
            **record(
                "hg-profile",
                "advanced:AS058:p1",
                {
                    "예약": "HG2029-00660",
                    "화주": "해온물류",
                    "장비": "HL-20GP-0066",
                    "계약": "CT-C-28001",
                },
                cases=["HG2029-00660"],
                identifiers=["HG2029-00660", "HL-20GP-0066", "CT-C-28001"],
            ),
            "kind": "case_context",
            "text": "화주 해온물류, 화물 세라믹 부품 12상자, 장비 HL-20GP-0066(20GP), 계약 CT-C-28001",
        },
        record(
            "ha-row",
            sources[2]["source_id"],
            {"항목": "화주·화물", "기재 내용": "해온물류 / 세라믹 부품"},
            cases=["HA28-C001"],
            identifiers=["HA28-C001", "HL-20GP-C001"],
        ),
        record(
            "is-row",
            sources[3]["source_id"],
            {"기록번호": "IS01-001-01", "대상": "HG2029-00660 예약의 실제 반출일"},
            identifiers=["IS01-001-01", "HG2029-00660"],
        ),
    ]
    jsonl(curated / "sources.jsonl", sources)
    jsonl(curated / "records" / "AS001.jsonl", rows[2:3])
    jsonl(curated / "records" / "AS007.jsonl", rows[3:4])
    jsonl(curated / "records" / "AS057.jsonl", rows[:1])
    jsonl(curated / "records" / "AS058.jsonl", rows[1:2])
    pages = [
        {
            "file_name": f"{item['book_id']}.pdf",
            "page": 1,
            "text": "화물 세라믹 부품 12상자, 장비 HL-20GP-0066, 계약 CT-C-28001"
            if item["book_id"] == "AS058"
            else "표 내용",
        }
        for item in sources
    ]
    stage = tmp_path / "stage"
    stage.mkdir()
    store = build_physical_store(curated, pages, stage, "fixture-source-fingerprint")
    assert store["rows"] == 3
    catalog = json.loads((stage / "catalog.json").read_text(encoding="utf-8"))
    assert all(
        column["name"] != "row_id" for table in catalog for column in table["columns"]
    )
    with __import__("sqlite3").connect(stage / "tables.sqlite3") as db:
        assert (
            db.execute(
                "select source_id from lineage where row_id='hg-row'"
            ).fetchone()[0]
            == "advanced:AS057:p1"
        )
    assert (
        len(json.loads((stage / "table-chunks.json").read_text(encoding="utf-8"))) == 1
    )
    (stage / "pages.json").write_text(json.dumps(pages), encoding="utf-8")
    manifest = build_records_index(curated, stage / "pages.json", stage)
    assert manifest["schema_version"] == manifest["version"] == 1
    assert manifest["families"]["HG"]["entities"] == 1
    assert manifest["families"]["HA"]["entities"] == 1
    assert manifest["families"]["IS"]["entities"] == 1
    check_database(stage / "tables.sqlite3")
    index = RecordIndex(stage)
    found = index.lookup([{"identifiers": ["HL-20GP-0066"], "constraints": []}])
    assert found["targets"][0]["status"] == "matched"
    assert found["targets"][0]["entities"][0]["sources"] == [
        {"source": "AS057:p0001", "role": "contract", "ordinal": 0},
        {"source": "AS058:p0001", "role": "events", "ordinal": 1},
    ]
    by_spec = index.lookup(
        [{"identifiers": [], "constraints": [{"field": "spec", "value": "20GP"}]}]
    )
    assert by_spec["targets"][0]["entities"][0]["id"] == "HG:HG2029-00660"
    with __import__("sqlite3").connect(stage / "records.sqlite3") as db:
        assert db.execute(
            "select value,source from attributes where entity='HG:HG2029-00660' and field='cargo'"
        ).fetchone() == ("세라믹 부품 12상자", "AS058:p0001")
    by_date = index.lookup(
        [
            {
                "identifiers": [],
                "constraints": [
                    {"field": "actual_discharge_date", "value": "2029-09-10"}
                ],
            }
        ]
    )
    assert by_date["targets"][0]["entities"][0]["id"] == "HG:HG2029-00660"
    assert index.discover("세라믹 정산", limit=3)
    index.close()


def test_fts_uses_runtime_bigram_and_identifier_tokenizer(tmp_path):
    curated = tmp_path / "curated"
    src = source("advanced:AS007:p1", "AS007", 1, "IS01 | 운항 대조 > IS01-001-01")
    jsonl(curated / "sources.jsonl", [src])
    jsonl(
        curated / "records" / "AS007.jsonl",
        [
            record(
                "is-row",
                src["source_id"],
                {"기록번호": "IS01-001-01", "처리": "AB-12344 운항 변경 확인"},
                identifiers=["IS01-001-01", "AB-12344"],
            )
        ],
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    pages = [{"file_name": "AS007.pdf", "page": 1, "text": "운항 대조"}]
    build_physical_store(curated, pages, stage, "fixture")
    (stage / "pages.json").write_text(json.dumps(pages), encoding="utf-8")
    build_records_index(curated, stage / "pages.json", stage)
    index = RecordIndex(stage)
    assert index.discover("AB-12344")
    index.close()


def test_typed_values_reject_invalid_calendar_dates_and_grouping():
    assert typed("2028-02-29", "일자")["value"] == "2028-02-29"
    assert typed("2029-02-29", "일자")["type"] == "TEXT"
    assert typed("USD 1,234.50", "청구액") == {
        "value": "1234.50",
        "unit": "USD",
        "state": "reported",
        "type": "DECIMAL",
    }
    assert typed("USD 12,34.50", "청구액")["type"] == "TEXT"
    assert typed("8,000", "토큰 수")["value"] == "8000"


def test_vector_chunks_resume_after_interrupted_batch(tmp_path, monkeypatch):
    import numpy as np
    import pytest
    import torch
    import app.ingestion.table_rag.vectors as vector_builder

    class FakeModel:
        prompts = {"document": "passage: ", "query": "query: "}
        dtype = torch.bfloat16
        device = "cpu"

        def __init__(self):
            self.max_seq_length = 0
            self.tokenizer = lambda texts, **kwargs: {
                "input_ids": [list(range(len(text))) for text in texts]
            }

        def get_sentence_embedding_dimension(self):
            return 2048

        def encode_document(self, texts, **kwargs):
            result = torch.zeros((len(texts), 2048), dtype=torch.float32)
            for index, text in enumerate(texts):
                result[index, 0] = 1.0
                result[index, 1] = len(text)
            return result

    monkeypatch.setattr(
        "sentence_transformers.SentenceTransformer", lambda *a, **k: FakeModel()
    )
    pages = [
        {"file_name": "AS001.pdf", "page": i + 1, "text": f"p{i}"} for i in range(257)
    ]
    chunks = [{"file_name": "AS001.pdf", "page": 1, "text": "table"}]
    pages_path = tmp_path / "pages.json"
    chunks_path = tmp_path / "chunks.json"
    pages_path.write_text(json.dumps(pages), encoding="utf-8")
    chunks_path.write_text(json.dumps(chunks), encoding="utf-8")
    progress = tmp_path / "progress"
    output = tmp_path / "vectors.npy"
    identity = {"fixture": "stable"}
    original = vector_builder._encode_document_batch
    calls = 0

    def interrupted(model, texts, *, batch_size=4):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated interruption")
        return original(model, texts, batch_size=batch_size)

    monkeypatch.setattr(vector_builder, "_encode_document_batch", interrupted)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        build_vectors(
            pages_path,
            chunks_path,
            output,
            progress_dir=progress,
            input_identity=identity,
        )
    monkeypatch.setattr(vector_builder, "_encode_document_batch", original)
    result = build_vectors(
        pages_path,
        chunks_path,
        output,
        progress_dir=progress,
        input_identity=identity,
    )
    vectors = np.load(output, allow_pickle=False)
    state = json.loads((progress / "build.json").read_text(encoding="utf-8"))
    assert result["reused_chunks"] == 1
    assert state["status"] == "complete"
    assert vectors.shape == (258, 2048)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)


def _write_vector_cache(cache, pages, chunks, embedding, *, bad_vector_hash=False):
    import numpy as np

    cache.mkdir()
    (cache / "pages.json").write_text(json.dumps(pages), encoding="utf-8")
    (cache / "table-chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
    vectors = np.zeros((len(pages) + len(chunks), 2048), dtype=np.float32)
    vectors[:, 0] = 1.0
    np.save(cache / "vectors.npy", vectors, allow_pickle=False)
    for filename, contents in (
        ("catalog.json", "[]"),
        ("tables.sqlite3", "fixture tables"),
        ("records.json", "{}"),
        ("records.sqlite3", "fixture records"),
    ):
        (cache / filename).write_text(contents, encoding="utf-8")
    store = {
        "files": {
            name: digest(cache / name)
            for name in ("catalog.json", "tables.sqlite3", "table-chunks.json")
        }
    }
    (cache / "store.json").write_text(json.dumps(store), encoding="utf-8")
    files = {
        name: digest(cache / name)
        for name in (
            "pages.json",
            "vectors.npy",
            "store.json",
            "catalog.json",
            "tables.sqlite3",
            "table-chunks.json",
            "records.json",
            "records.sqlite3",
        )
    }
    if bad_vector_hash:
        files["vectors.npy"] = "0" * 64
    (cache / "corpus.json").write_text(
        json.dumps(
            {
                "files": files,
                "embedding": embedding,
                "pipeline_stages": {"vectors": "fixture-stage"},
                "counts": {
                    "pages": len(pages),
                    "table_documents": len(chunks),
                },
            }
        ),
        encoding="utf-8",
    )


def test_cached_vectors_must_match_manifest_declared_sha(tmp_path):
    import pytest

    pages = [{"file_name": "AS001.pdf", "page": 1, "text": "page"}]
    chunks = []
    input_pages = tmp_path / "pages.json"
    input_chunks = tmp_path / "chunks.json"
    input_pages.write_text(json.dumps(pages), encoding="utf-8")
    input_chunks.write_text(json.dumps(chunks), encoding="utf-8")
    contract = embedding_contract("cpu")
    cache = tmp_path / "cache"
    _write_vector_cache(cache, pages, chunks, contract, bad_vector_hash=True)
    with pytest.raises(ValueError, match="declared SHA mismatch: vectors.npy"):
        build_vectors(
            input_pages,
            input_chunks,
            tmp_path / "out.npy",
            cache_bundle=cache,
            input_identity={
                "embedding": contract,
                "builder_sha256": "fixture-stage",
            },
        )


def test_legacy_vector_cache_without_full_contract_is_rejected(tmp_path):
    import pytest

    pages = [{"file_name": "AS001.pdf", "page": 1, "text": "page"}]
    chunks = []
    input_pages = tmp_path / "pages.json"
    input_chunks = tmp_path / "chunks.json"
    input_pages.write_text(json.dumps(pages), encoding="utf-8")
    input_chunks.write_text(json.dumps(chunks), encoding="utf-8")
    legacy_contract = {
        "model_id": "nlpai-lab/KURE-v3",
        "revision": "73d5e710bc0b1f324a60746922cbad6d38f480d4",
        "dimensions": 2048,
        "query_max_length": 2048,
        "page_max_length": 8192,
        "table_max_length": 2048,
        "query_prefix": "query: ",
        "document_prefix": "passage: ",
        "normalized": True,
    }
    cache = tmp_path / "cache"
    _write_vector_cache(cache, pages, chunks, legacy_contract)
    with pytest.raises(ValueError, match="complete compatible embedding contract"):
        build_vectors(
            input_pages,
            input_chunks,
            tmp_path / "out.npy",
            cache_bundle=cache,
            input_identity={
                "embedding": embedding_contract("cpu"),
                "builder_sha256": "fixture-stage",
            },
        )


def test_vector_cache_with_full_contract_is_reused(tmp_path):
    import numpy as np

    pages = [{"file_name": "AS001.pdf", "page": 1, "text": "page"}]
    chunks = []
    input_pages = tmp_path / "pages.json"
    input_chunks = tmp_path / "chunks.json"
    input_pages.write_text(json.dumps(pages), encoding="utf-8")
    input_chunks.write_text(json.dumps(chunks), encoding="utf-8")
    contract = embedding_contract("cpu")
    cache = tmp_path / "cache"
    _write_vector_cache(cache, pages, chunks, contract)
    output = tmp_path / "out.npy"
    result = build_vectors(
        input_pages,
        input_chunks,
        output,
        cache_bundle=cache,
        input_identity={
            "embedding": contract,
            "builder_sha256": "fixture-stage",
        },
    )
    assert result["reused"] is True
    assert np.load(output, allow_pickle=False).shape == (1, 2048)
    assert digest(output) == digest(cache / "vectors.npy")


def test_output_build_lock_rejects_a_second_writer(tmp_path):
    import pytest
    from app.ingestion.table_rag_builder import _output_build_lock

    output = tmp_path / "bundle"
    with _output_build_lock(output):
        with pytest.raises(FileExistsError, match="Another build holds"):
            with _output_build_lock(output):
                raise AssertionError("second writer acquired the output lock")
    assert not (tmp_path / ".bundle.build.lock").exists()


def test_completed_bundle_from_different_pipeline_requires_new_output(
    tmp_path, monkeypatch
):
    import hashlib
    import pytest

    documents = tmp_path / "documents"
    documents.mkdir()
    pdf = documents / "AS001.pdf"
    pdf.write_bytes(b"not parsed because output conflict is checked first")
    import app.ingestion.table_rag.fingerprints as fingerprints

    monkeypatch.setattr(
        fingerprints,
        "curation_environment",
        lambda: {"extractor": "pdftotext fixture", "pydantic_version": "fixture"},
    )
    source_identity = [{"file_name": pdf.name, "sha256": digest(pdf)}]
    source_fingerprint = hashlib.sha256(
        json.dumps(source_identity, separators=(",", ":")).encode()
    ).hexdigest()
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "corpus.json").write_text(
        json.dumps(
            {
                "inputs": {"source_pdfs_sha256": source_fingerprint},
                "pipeline_sha256": "previous-pipeline",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="different pipeline fingerprint"):
        build_bundle(documents, output)


def test_stale_work_directory_source_pdf_fingerprint_is_rejected(tmp_path):
    import hashlib
    import json
    import pytest
    from app.ingestion.table_rag_builder import _assert_work_dir_compatible

    work = tmp_path / "work"
    stage = work / "bundle"
    stage.mkdir(parents=True)
    old_pdf = b"old synthetic PDF"
    new_pdf = b"new synthetic PDF"
    old_identity = [
        {"file_name": "AS001.pdf", "sha256": hashlib.sha256(old_pdf).hexdigest()}
    ]
    new_identity = [
        {"file_name": "AS001.pdf", "sha256": hashlib.sha256(new_pdf).hexdigest()}
    ]
    old_pages = b"old generated pages"
    (stage / "pages.json").write_bytes(old_pages)
    (stage / "pages-input.json").write_text(
        json.dumps({"inputs": old_identity, "builder_sha256": "pages-v2"}),
        encoding="utf-8",
    )
    stage_codes = {"pages": "pages-v2", "overall": "pipeline-v2"}

    with pytest.raises(ValueError, match="source PDF input fingerprint changed"):
        _assert_work_dir_compatible(work, stage_codes, new_identity, "new-source")

    assert (stage / "pages.json").read_bytes() == old_pages


def test_unidentified_vector_progress_is_preserved_and_rejected(tmp_path):
    import pytest
    from app.ingestion.table_rag_builder import _assert_work_dir_compatible

    work = tmp_path / "work"
    progress = work / "bundle" / ".vectors-old"
    progress.mkdir(parents=True)
    old_chunk = progress / "pages-00000-00001.npy.tmp"
    old_chunk.write_bytes(b"partial old vector output")

    with pytest.raises(ValueError, match="vector progress has no build identity"):
        _assert_work_dir_compatible(
            work,
            {
                "pages": "pages",
                "physical": "physical",
                "records": "records",
                "vectors": "vectors",
                "overall": "overall",
            },
            [],
            "source",
        )

    assert old_chunk.read_bytes() == b"partial old vector output"


def test_curation_stage_fingerprint_tracks_external_parser_identity(monkeypatch):
    import app.ingestion.table_rag_builder as builder
    import app.ingestion.table_rag.fingerprints as fingerprints

    monkeypatch.setattr(
        fingerprints,
        "curation_environment",
        lambda: {"extractor": "pdftotext fixture", "pydantic_version": "fixture"},
    )
    before = builder.stage_fingerprints()
    environment = fingerprints.curation_environment()
    monkeypatch.setattr(
        fingerprints,
        "curation_environment",
        lambda: {**environment, "extractor": "pdftotext changed"},
    )
    after = builder.stage_fingerprints()
    assert after["curation"] != before["curation"]
    assert after["overall"] != before["overall"]


def test_stale_work_directory_is_preserved_and_rejected(tmp_path, monkeypatch):
    import pytest
    import app.ingestion.table_rag_builder as builder

    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "AS001.pdf").write_bytes(b"synthetic input")
    work = tmp_path / "work"
    stage = work / "bundle"
    stage.mkdir(parents=True)
    old_pages = b"old generated pages"
    old_identity = b'{"builder_sha256":"old-pages"}\n'
    (stage / "pages.json").write_bytes(old_pages)
    (stage / "pages-input.json").write_bytes(old_identity)
    output = tmp_path / "output"

    monkeypatch.setattr(
        builder,
        "stage_fingerprints",
        lambda: {
            "curation": "new-curation",
            "pages": "new-pages",
            "physical": "new-physical",
            "records": "new-records",
            "vectors": "new-vectors",
            "orchestration": "new-orchestration",
            "overall": "new-overall",
        },
    )
    monkeypatch.setattr(
        builder,
        "curate_dataset",
        lambda *args, **kwargs: pytest.fail("stale work must fail before curation"),
    )

    with pytest.raises(ValueError, match="pages stage fingerprint changed"):
        builder.build_bundle(documents, output, work_dir=work)

    assert (stage / "pages.json").read_bytes() == old_pages
    assert (stage / "pages-input.json").read_bytes() == old_identity
    assert not (work / ".table-rag-pipeline.json").exists()
    assert not output.exists()
