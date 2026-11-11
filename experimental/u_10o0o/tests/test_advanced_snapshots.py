from hashlib import sha256

import pytest

from experimental.u_10o0o import advanced_snapshots as snapshots


def test_snapshot_reuse_rejects_a_partial_output_manifest(tmp_path):
    inputs = {"test": "snapshot"}
    database = tmp_path / "advanced.sqlite3"
    database.write_bytes(b"test database")
    snapshots._write_snapshot_manifest(
        tmp_path,
        snapshot_type=snapshots.HG_TYPE,
        inputs=inputs,
        output_names=(database.name,),
        counts={},
        config={},
    )
    assert snapshots._published_snapshot(tmp_path, inputs, snapshots.HG_TYPE)

    snapshots._write_snapshot_manifest(
        tmp_path,
        snapshot_type=snapshots.HG_TYPE,
        inputs=inputs,
        output_names=(),
        counts={},
        config={},
    )
    with pytest.raises(ValueError, match="출력 파일"):
        snapshots._published_snapshot(tmp_path, inputs, snapshots.HG_TYPE)


def test_pdf_provenance_checks_current_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "DATA_DIR", tmp_path)
    directory = tmp_path / "documents"
    directory.mkdir()
    document = directory / "AS001.pdf"
    original = b"original fixture"
    document.write_bytes(original)
    metadata = {document.name: {"sha256": sha256(original).hexdigest()}}
    snapshots._verify_pdfs(metadata)
    document.write_bytes(b"changed fixture")
    with pytest.raises(ValueError, match="PDF 파일의 지문"):
        snapshots._verify_pdfs(metadata)
