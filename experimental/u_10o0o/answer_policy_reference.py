"""Frozen experiment helper; independent of concurrent product evaluator changes."""

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any


def _unquoted_pages(reference: Mapping[str, Any]) -> set[tuple[str, int]]:
    pages = set()
    for group in reference.get("evidence_groups") or []:
        for source in group:
            book, page = source.get("book_id"), source.get("page")
            quote = source.get("quote")
            if (
                isinstance(book, str)
                and book
                and type(page) is int
                and page > 0
                and not (isinstance(quote, str) and quote.strip())
            ):
                pages.add((book, page))
    return pages


def attach_reference_pages(
    references: dict[str, dict[str, Any]],
    directory: Path,
    *,
    snapshot_sha256: str | None,
) -> str | None:
    """Enrich in-memory grading references and return the page file fingerprint."""
    by_question = {qid: _unquoted_pages(ref) for qid, ref in references.items()}
    needed = set().union(*by_question.values())
    if not needed:
        return None

    try:
        manifest = (directory / "corpus.json").read_bytes()
        if snapshot_sha256 and sha256(
            manifest
        ).hexdigest() != snapshot_sha256.removeprefix("sha256:"):
            raise ValueError("Reference bundle does not match the saved run snapshot")
        pages_bytes = (directory / "pages.json").read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(
            "Judge needs the saved run's TableRAG bundle for page-only references. "
            "Pass --data-directory if it has moved."
        ) from exc
    digest = sha256(pages_bytes).hexdigest()
    if json.loads(manifest)["files"]["pages.json"] != digest:
        raise ValueError("Reference pages do not match the bundle fingerprint")

    pages = {}
    for source in json.loads(pages_bytes):
        book_id = source.get("book_id") or Path(source["file_name"]).stem
        key = (book_id, source["page"])
        if key in needed and source["text"].strip():
            pages[key] = {"book_id": key[0], "page": key[1], "text": source["text"]}
    if missing := sorted(needed - pages.keys()):
        preview = ", ".join(f"{book} p.{page}" for book, page in missing[:5])
        raise ValueError(
            f"Reference pages missing or empty in bundle ({len(missing)}): {preview}"
        )

    for qid, sources in by_question.items():
        if sources:
            references[qid] = {
                **references[qid],
                "reference_pages": [pages[source] for source in sorted(sources)],
            }
    return f"sha256:{digest}"
