import re
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest

from experimental.u_10o0o.advanced_is_windows import (
    EMBEDDING_DIM,
    MODEL_ID,
    MODEL_REVISION,
    OUTPUT_FILES,
    SCHEMA_VERSION,
    SNAPSHOT_TYPE,
    WINDOW_STEP,
    WINDOW_TOKENS,
    ISWindowDense,
    _json_bytes,
    _model_config,
    _page_snapshot_sha,
    _sha_file,
    _window_specs,
)


class WordTokenizer:
    is_fast = True

    def __init__(self, *, leading_zero_offset=False):
        self.leading_zero_offset = leading_zero_offset

    def __call__(
        self,
        text,
        *,
        add_special_tokens,
        return_offsets_mapping,
        truncation,
        verbose,
    ):
        assert not add_special_tokens
        assert return_offsets_mapping
        assert not truncation
        assert not verbose
        offsets = [(match.start(), match.end()) for match in re.finditer(r"\S+", text)]
        ids = list(range(len(offsets)))
        if self.leading_zero_offset:
            ids.insert(0, -1)
            offsets.insert(0, (0, 0))
        return {"input_ids": ids, "offset_mapping": offsets}


def _unit(score):
    return np.asarray(
        [score, (1 - score**2) ** 0.5, *([0.0] * (EMBEDDING_DIM - 2))], dtype=np.float32
    )


def _window_row(window_index, page_index, page, spec):
    char_start = spec["char_start"]
    char_end = spec["char_end"]
    text = page["text"][char_start:char_end]
    return {
        "window_index": window_index,
        "page_index": page_index,
        "source_id": f"advanced:{Path(page['file_name']).stem}:p{page['page']}",
        "book_id": Path(page["file_name"]).stem,
        "file_name": page["file_name"],
        "page": page["page"],
        **spec,
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
    }


def _snapshot(tmp_path, pages, base_vectors, rows, window_vectors, is_source_ids):
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "windows.jsonl").write_bytes(b"".join(_json_bytes(row) for row in rows))
    np.save(root / "vectors.npy", window_vectors, allow_pickle=False)
    outputs = {name: _sha_file(root / name) for name in OUTPUT_FILES}
    inputs = {
        "code_sha256": _sha_file(Path(__file__).parents[1] / "advanced_is_windows.py"),
        "page_snapshot_sha256": _page_snapshot_sha(pages),
        "base_vectors_content_sha256": sha256(
            np.ascontiguousarray(base_vectors).tobytes()
        ).hexdigest(),
        "model": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "embedding_dimension": EMBEDDING_DIM,
        "is_source_ids": is_source_ids,
        "pdf_source_sha256": {
            pages[index]["file_name"]: "a" * 64
            for index in range(len(pages))
            if f"advanced:{Path(pages[index]['file_name']).stem}:p{pages[index]['page']}"
            in is_source_ids
        },
    }
    config = _model_config()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_type": SNAPSHOT_TYPE,
        "status": "complete",
        "snapshot_id": sha256(
            _json_bytes({"inputs": inputs, "outputs": outputs, "config": config})
        ).hexdigest(),
        "inputs": inputs,
        "outputs": outputs,
        "counts": {
            "source_pages": len(is_source_ids),
            "books": len(inputs["pdf_source_sha256"]),
            "windows": len(rows),
        },
        "config": config,
    }
    (root / "manifest.json").write_bytes(_json_bytes(manifest))
    return root


def test_window_specs_use_256_content_tokens_and_64_overlap():
    words = [f"w{index:03d}" for index in range(449)]
    text = "  " + " ".join(words) + "  "

    windows = _window_specs(text, WordTokenizer())

    assert [row["token_start"] for row in windows] == [0, WINDOW_STEP, 2 * WINDOW_STEP]
    assert [(row["token_end"] - row["token_start"]) for row in windows] == [
        WINDOW_TOKENS,
        WINDOW_TOKENS,
        65,
    ]
    assert windows[1]["token_start"] == windows[0]["token_end"] - 64
    assert windows[2]["token_start"] == windows[1]["token_end"] - 64
    assert windows[0]["char_start"] == 0
    assert windows[-1]["char_end"] == len(text)
    covered = [False] * len(text)
    for row in windows:
        for index in range(row["char_start"], row["char_end"]):
            covered[index] = True
    assert all(
        character.isspace() or covered[index] for index, character in enumerate(text)
    )


@pytest.mark.parametrize(
    ("content_count", "expected_ranges"),
    [
        (193, [(0, 193)]),
        (256, [(0, 256)]),
        (385, [(0, 256), (192, 385)]),
    ],
)
def test_windowing_stops_when_previous_window_reaches_page_end(
    content_count, expected_ranges
):
    text = " ".join(f"w{index:03d}" for index in range(content_count))

    windows = _window_specs(text, WordTokenizer())

    assert [
        (row["token_start"], row["token_end"]) for row in windows
    ] == expected_ranges


def test_short_page_remains_one_exact_original_substring():
    text = "\n  one two three  \n"

    windows = _window_specs(text, WordTokenizer())

    assert len(windows) == 1
    assert windows[0]["char_start"] == 0
    assert windows[0]["char_end"] == len(text)
    assert windows[0]["token_count"] == 3


def test_zero_offset_token_is_not_counted_as_content_or_lost_from_source():
    text = "one two"

    (window,) = _window_specs(text, WordTokenizer(leading_zero_offset=True))

    assert (window["token_start"], window["token_end"]) == (0, 2)
    assert window["token_count"] == 2
    assert (window["tokenizer_start"], window["tokenizer_end"]) == (0, 3)
    assert text[window["char_start"] : window["char_end"]] == text


def test_zero_offset_token_does_not_drop_original_page_text():
    class MissingFirstWordTokenizer(WordTokenizer):
        def __call__(self, text, **kwargs):
            encoded = super().__call__(text, **kwargs)
            encoded["offset_mapping"][0] = (0, 0)
            return encoded

    text = "one two"
    (window,) = _window_specs(text, MissingFirstWordTokenizer())

    assert window["token_count"] == 1
    assert text[window["char_start"] : window["char_end"]] == text


def test_rank_uses_is_window_max_collapses_parents_and_keeps_non_is_page_scores(
    tmp_path,
):
    is_one_text = " ".join(f"word{index}" for index in range(300))
    pages = [
        {"file_name": "AS000.pdf", "page": 1, "text": "other page"},
        {"file_name": "AS001.pdf", "page": 1, "text": is_one_text},
        {"file_name": "AS002.pdf", "page": 1, "text": "IS two"},
        {"file_name": "AS003.pdf", "page": 1, "text": "other three"},
    ]
    base_vectors = np.stack([_unit(score) for score in (0.94, 0.999, 0.990, 0.985)])
    is_source_ids = ["advanced:AS001:p1", "advanced:AS002:p1"]
    rows = [
        _window_row(index, 1, pages[1], spec)
        for index, spec in enumerate(_window_specs(is_one_text, WordTokenizer()))
    ]
    rows.append(
        _window_row(
            len(rows), 2, pages[2], _window_specs(pages[2]["text"], WordTokenizer())[0]
        )
    )
    window_vectors = np.stack([_unit(score) for score in (0.97, 0.96, 0.95)])
    root = _snapshot(tmp_path, pages, base_vectors, rows, window_vectors, is_source_ids)

    ranked = ISWindowDense(pages, base_vectors, root).rank(_unit(1.0), top_k=3)

    assert [page["file_name"] for page, _ in ranked] == [
        "AS003.pdf",
        "AS001.pdf",
        "AS002.pdf",
    ]
    assert [score for _, score in ranked] == pytest.approx([0.985, 0.97, 0.95])


def test_snapshot_rejects_mismatched_original_pages(tmp_path):
    pages = [{"file_name": "AS001.pdf", "page": 1, "text": "IS one"}]
    base_vectors = np.stack([_unit(1.0)])
    rows = [
        _window_row(0, 0, pages[0], _window_specs(pages[0]["text"], WordTokenizer())[0])
    ]
    root = _snapshot(
        tmp_path,
        pages,
        base_vectors,
        rows,
        np.stack([_unit(1.0)]),
        ["advanced:AS001:p1"],
    )

    pages[0]["text"] = "IS altered"
    with pytest.raises(ValueError, match="source text hash"):
        ISWindowDense(pages, base_vectors, root)
