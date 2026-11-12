import asyncio
import hashlib
import sqlite3
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

from app.rag.readiness import ReadinessProbe, build_readiness_probe


def _make_bundle(tmp_path: Path):
    records_path = tmp_path / "records.sqlite3"
    tables_path = tmp_path / "tables.sqlite3"
    with sqlite3.connect(records_path) as connection:
        connection.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY)")
    table_id = "AS001:p0001:t001"
    with sqlite3.connect(tables_path) as connection:
        connection.execute(f'CREATE TABLE "{table_id}" (value TEXT)')
    vectors_path = tmp_path / "vectors.npy"
    np.save(vectors_path, np.asarray([[1.0, 0.0]], dtype=np.float32))
    vectors = np.load(vectors_path, mmap_mode="r", allow_pickle=False)
    table_store = SimpleNamespace(
        catalog={table_id: {"table_id": table_id}},
        directory=tmp_path,
    )
    corpus = SimpleNamespace(
        documents=(object(),),
        vectors=vectors,
        embedding={"dimensions": 2},
        table_store=table_store,
    )
    settings = SimpleNamespace(
        vector_backend="numpy",
        run_directory=tmp_path / "runs",
    )
    return records_path, tables_path, vectors, corpus, settings


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_probe_checks_read_only_sqlite_vectors_and_run_storage(tmp_path):
    records, tables, vectors, corpus, settings = _make_bundle(tmp_path)
    before = (_digest(records), _digest(tables))
    probe = build_readiness_probe(tmp_path, corpus, settings)

    status = asyncio.run(probe.get())

    assert status.ready is True
    assert status.checks == {
        "resources": True,
        "records": True,
        "tables": True,
        "vector_store": True,
        "run_store": True,
    }
    assert (_digest(records), _digest(tables)) == before
    assert list(settings.run_directory.iterdir()) == []
    asyncio.run(probe.aclose())
    vectors._mmap.close()


def test_probe_concurrent_callers_share_one_check():
    started, release = Event(), Event()
    calls = []

    def check():
        calls.append(True)
        started.set()
        assert release.wait(2)
        return True

    probe = ReadinessProbe({"records": check})

    async def run():
        first = asyncio.create_task(probe.get())
        assert await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(probe.get())
        await asyncio.sleep(0)
        release.set()
        return await asyncio.gather(first, second)

    statuses = asyncio.run(run())
    assert len(calls) == 1
    assert statuses[0].ready and statuses[1].ready


def test_cancelled_waiter_does_not_cancel_shared_probe():
    started, release = Event(), Event()
    calls = []

    def check():
        calls.append(True)
        started.set()
        assert release.wait(2)
        return True

    probe = ReadinessProbe({"records": check})

    async def run():
        abandoned = asyncio.create_task(probe.get())
        assert await asyncio.to_thread(started.wait, 1)
        abandoned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await abandoned
        remaining = asyncio.create_task(probe.get())
        release.set()
        return await remaining

    status = asyncio.run(run())
    assert status.ready is True
    assert len(calls) == 1


def test_aclose_rejects_new_checks_and_joins_running_probe():
    started, release, finished = Event(), Event(), Event()
    calls = []

    def check():
        calls.append(True)
        started.set()
        assert release.wait(2)
        finished.set()
        return True

    probe = ReadinessProbe({"records": check})

    async def run():
        pending = asyncio.create_task(probe.get())
        assert await asyncio.to_thread(started.wait, 1)
        closing = asyncio.create_task(probe.aclose())
        await asyncio.sleep(0)
        unavailable = await probe.get()
        assert unavailable.ready is False
        assert unavailable.checks == {"records": False}
        assert not closing.done()
        release.set()
        await closing
        assert finished.is_set()
        assert (await probe.get()).ready is False
        return pending

    pending = asyncio.run(run())
    assert pending.done()
    assert len(calls) == 1


def test_failed_sqlite_and_storage_checks_are_safe_and_recover_after_ttl(
    tmp_path, monkeypatch
):
    from app.rag import readiness

    records, tables, vectors, corpus, settings = _make_bundle(tmp_path)
    secret_fragment = "credential-path-secret"
    broken_records = tmp_path / f"{secret_fragment}.sqlite3"
    broken_records.write_text("not a sqlite database", encoding="utf-8")
    records.unlink()
    broken_records.rename(records)
    failed_run_directory = tmp_path / "not-a-directory"
    failed_run_directory.write_text("file", encoding="utf-8")
    settings.run_directory = failed_run_directory
    probe = build_readiness_probe(tmp_path, corpus, settings)

    failed = asyncio.run(probe.get())
    assert failed.ready is False
    assert failed.checks["records"] is False
    assert failed.checks["run_store"] is False
    assert secret_fragment not in repr(failed)
    assert list(tmp_path.glob(".aden-readiness-*")) == []

    now = [100.0]
    monkeypatch.setattr(readiness, "monotonic", lambda: now[0])
    check_state = [False]
    retry_probe = ReadinessProbe({"storage": lambda: check_state[0]}, ttl_seconds=10)
    first = asyncio.run(retry_probe.get())
    assert first.ready is False
    check_state[0] = True
    assert asyncio.run(retry_probe.get()).ready is False
    now[0] += 10.01
    assert asyncio.run(retry_probe.get()).ready is True

    asyncio.run(probe.aclose())
    asyncio.run(retry_probe.aclose())
    vectors._mmap.close()


def test_closed_local_mmap_is_rejected_before_sample_access(tmp_path):
    _, _, vectors, corpus, _ = _make_bundle(tmp_path)
    vectors._mmap.close()

    from app.rag.readiness import _check_local_vectors

    with pytest.raises(ValueError, match="unavailable"):
        _check_local_vectors(corpus)


def test_probe_construction_does_not_inspect_fake_resources(tmp_path):
    settings = SimpleNamespace()
    corpus = object()
    probe = build_readiness_probe(tmp_path, corpus, settings)
    assert set(probe._checks) == {
        "resources",
        "records",
        "tables",
        "vector_store",
        "run_store",
    }
    asyncio.run(probe.aclose())


def test_run_store_probe_removes_temporary_file_when_fsync_fails(tmp_path, monkeypatch):
    from app.rag import readiness

    def fail_fsync(_descriptor):
        raise OSError("sensitive storage details")

    monkeypatch.setattr(readiness.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="sensitive"):
        readiness._check_run_store(tmp_path)
    assert list(tmp_path.iterdir()) == []
