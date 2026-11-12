"""Exercise local Qdrant HTTP and read-only SQLite in CI."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from qdrant_client import QdrantClient, models

DEFAULT_URL = "http://127.0.0.1:6333"


def _local_url() -> str:
    url = os.environ.get("QDRANT_URL", DEFAULT_URL)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CI integration only permits a local Qdrant HTTP endpoint")
    return url


def _check_sqlite_fixture(directory: Path) -> None:
    path = directory / "records.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE entities (id TEXT PRIMARY KEY, source_id TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO entities (id, source_id) VALUES (?, ?)",
            ("fixture-1", "AS001:p0001"),
        )

    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            "SELECT source_id FROM entities WHERE id = ?", ("fixture-1",)
        ).fetchone()
        assert row == ("AS001:p0001",)
        try:
            connection.execute(
                "INSERT INTO entities VALUES (?, ?)", ("unexpected", "write")
            )
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("Read-only SQLite accepted a write")


def _wait_for_qdrant(client: QdrantClient) -> None:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            client.get_collections()
            return
        except Exception as error:  # Service container may still be starting.
            last_error = error
            time.sleep(1)
    raise RuntimeError("Local Qdrant did not become reachable") from last_error


def _check_exact_search(client: QdrantClient) -> None:
    collection = f"aden_ci_{uuid4().hex}"
    client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE),
    )
    try:
        client.upsert(
            collection_name=collection,
            points=[
                models.PointStruct(id=1, vector=[1.0, 0.0, 0.0], payload={"row": 1}),
                models.PointStruct(id=2, vector=[0.0, 1.0, 0.0], payload={"row": 2}),
            ],
            wait=True,
        )
        result = client.query_points(
            collection_name=collection,
            query=[1.0, 0.0, 0.0],
            search_params=models.SearchParams(exact=True),
            limit=2,
            with_payload=True,
            with_vectors=False,
        )
        assert [point.id for point in result.points] == [1, 2]
        assert result.points[0].score == 1.0
        assert result.points[0].payload == {"row": 1}
    finally:
        client.delete_collection(collection_name=collection)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="aden-ci-") as temporary:
        _check_sqlite_fixture(Path(temporary))

    client = QdrantClient(url=_local_url(), timeout=2, check_compatibility=False)
    try:
        _wait_for_qdrant(client)
        _check_exact_search(client)
    finally:
        client.close()

    print("Local Qdrant exact search and read-only SQLite fixture passed.")


if __name__ == "__main__":
    main()
