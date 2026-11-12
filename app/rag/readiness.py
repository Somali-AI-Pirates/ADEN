"""Cached, read-only checks for the resources required by TableRAG."""

from __future__ import annotations

import asyncio
import math
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable, Mapping

import numpy as np

from app.rag.lifecycle import await_to_completion

Check = Callable[[], bool | None]


@dataclass(frozen=True)
class ReadinessStatus:
    """Public-safe component booleans; detailed errors remain internal."""

    ready: bool
    checks: dict[str, bool]


class ReadinessProbe:
    """Run checks once per TTL and share one in-flight probe across callers."""

    def __init__(
        self,
        checks: Mapping[str, Check],
        *,
        ttl_seconds: float = 10.0,
    ) -> None:
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("Readiness cache lifetime must be positive and finite")
        if any(not isinstance(name, str) or not name for name in checks):
            raise ValueError("Readiness check names must be non-empty strings")
        if not checks:
            raise ValueError("At least one readiness check is required")
        if any(not callable(check) for check in checks.values()):
            raise TypeError("Readiness checks must be callable")
        self._checks = dict(checks)
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[ReadinessStatus] | None = None
        self._cached: ReadinessStatus | None = None
        self._expires_at = 0.0
        self._closed = False

    async def get(self) -> ReadinessStatus:
        async with self._lock:
            if self._closed:
                return self._unavailable()
            if self._cached is not None and monotonic() < self._expires_at:
                return self._copy_status(self._cached)
            if self._task is None:
                self._task = asyncio.create_task(self._probe())
            task = self._task

        # A disconnected health client must not cancel the shared probe thread.
        status = await asyncio.shield(task)
        async with self._lock:
            if self._closed:
                return self._unavailable()
            return self._copy_status(status)

    async def _probe(self) -> ReadinessStatus:
        try:
            checks = await asyncio.to_thread(self._run_checks)
        except Exception:
            checks = {name: False for name in self._checks}
        status = ReadinessStatus(ready=all(checks.values()), checks=checks)
        async with self._lock:
            if self._closed:
                status = self._unavailable()
            else:
                self._cached = status
                self._expires_at = monotonic() + self._ttl_seconds
            if self._task is asyncio.current_task():
                self._task = None
        return status

    def _run_checks(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for name, check in self._checks.items():
            try:
                outcome = check()
                results[name] = outcome is True or outcome is None
            except Exception:
                # Never expose storage paths, remote URLs, or exception text.
                results[name] = False
        return results

    @staticmethod
    def _copy_status(status: ReadinessStatus) -> ReadinessStatus:
        return ReadinessStatus(ready=status.ready, checks=dict(status.checks))

    def _unavailable(self) -> ReadinessStatus:
        checks = {name: False for name in self._checks}
        return ReadinessStatus(ready=False, checks=checks)

    async def aclose(self) -> None:
        """Reject new probes and join a running probe before resources close."""
        async with self._lock:
            self._closed = True
            task = self._task
            self._cached = None
            self._expires_at = 0.0
        if task is not None and not task.done():
            _, cancelled = await await_to_completion(task)
            if cancelled:
                raise asyncio.CancelledError


def _check_sqlite(path: Path, expected_table: str) -> None:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as connection:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (expected_table,),
        ).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("Required table is unavailable")
        identifier = row[0].replace('"', '""')
        connection.execute(f'SELECT * FROM "{identifier}" LIMIT 0')


def _check_records(path: Path) -> None:
    _check_sqlite(path, "entities")


def _check_tables(table_store) -> None:
    if not table_store.catalog:
        raise sqlite3.DatabaseError("Physical table catalog is empty")
    table_id = next(iter(table_store.catalog))
    _check_sqlite(table_store.directory / "tables.sqlite3", table_id)


def _check_local_vectors(corpus) -> None:
    vectors = corpus.vectors
    documents = corpus.documents
    dimensions = corpus.embedding["dimensions"]
    expected_shape = (len(documents), dimensions)
    memory_map = getattr(vectors, "_mmap", None)
    if (
        not isinstance(vectors, np.ndarray)
        or vectors.dtype != np.dtype(np.float32)
        or vectors.shape != expected_shape
        or vectors.flags.writeable
        or not documents
        or dimensions <= 0
        or (memory_map is not None and memory_map.closed)
    ):
        raise ValueError("Local vector bundle is unavailable")

    sample_indexes = {0, len(documents) // 2, len(documents) - 1}
    for index in sample_indexes:
        row = np.asarray(vectors[index], dtype=np.float32)
        if row.shape != (dimensions,) or not np.isfinite(row).all():
            raise ValueError("Local vector sample is unavailable")


def _check_run_store(directory: str | Path) -> None:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".aden-readiness-", dir=root)
    descriptor_open = True
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor_open = False
            stream.write(b"table-rag-readiness\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        try:
            if descriptor_open:
                os.close(descriptor)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass


def build_readiness_probe(directory: str | Path, corpus, settings) -> ReadinessProbe:
    """Bind checks lazily so constructing the probe does not inspect resources."""
    root = Path(directory).resolve()

    def check_run_store() -> bool:
        run_directory = getattr(settings, "run_directory", None)
        if run_directory is None:
            return False
        _check_run_store(run_directory)
        return True

    def check_vector_store() -> bool:
        backend = getattr(settings, "vector_backend", None)
        if backend == "numpy":
            _check_local_vectors(corpus)
            return True
        if backend == "qdrant":
            from app.rag.qdrant_index import check_qdrant_readiness

            check_qdrant_readiness(
                root,
                corpus,
                collection=getattr(settings, "collection", "advanced_table_rag"),
                credentials=getattr(settings, "credentials", None),
                timeout=2,
            )
            return True
        return False

    return ReadinessProbe(
        {
            "resources": lambda: True,
            "records": lambda: _check_records(root / "records.sqlite3"),
            "tables": lambda: _check_tables(corpus.table_store),
            "vector_store": check_vector_store,
            "run_store": check_run_store,
        }
    )
