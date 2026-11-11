from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from experimental.u_10o0o.fresh_record_hybrid import retrieval as runner
from experimental.u_10o0o.fresh_record_hybrid.models import TokenUsage


def _unit(unit_id: str, text: str, *, corpus: str = "B") -> runner.Unit:
    return runner.Unit(unit_id=unit_id, text=text, sources=(unit_id,), corpus=corpus)


def test_bm25_keeps_repeated_term_frequency() -> None:
    docs = [
        _unit("A", "보험 보험 보험 지급"),
        _unit("B", "보험 지급"),
    ]
    tokens = runner.tokenize("보험 보험")
    assert tokens.count("word:보험") == 2
    scores = runner.BM25Index(docs).scores("보험")
    assert scores[0] > scores[1]


def test_complete_code_survives_adjacent_korean_particle() -> None:
    relation_index = {
        "code_to_events": {"HG123": ["event:HG123"]},
        "events": {
            "event:HG123": {
                "relations": [
                    {"code": "HG-123", "normalized_code": "HG123"},
                ]
            }
        },
    }
    assert runner.normalize_code("HG-123") == "HG123"
    tokens = runner.tokenize("HG-123에서 확인")
    assert "code:exact:HG-123" in tokens
    assert runner.find_explicit_codes("HG-123에서 확인", relation_index) == [
        ("HG123", 0)
    ]
    assert runner.find_explicit_codes("XHG-123", relation_index) == []
    assert runner.find_explicit_codes("HG-1234", relation_index) == []
    assert runner.find_explicit_codes("HG-123-04", relation_index) == []


def test_rrf_ties_are_ordered_by_unit_id() -> None:
    assert runner.reciprocal_rank_fusion([["u-b", "u-a"], ["u-a", "u-b"]]) == [
        "u-a",
        "u-b",
    ]


class _FakeTokenizer:
    pass


class _FakeEmbedder:
    dimension = 2
    tokenizer = _FakeTokenizer()
    query_prefix = ""

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.encode_calls = 0

    def token_usage(self, text: str, *, query: bool) -> TokenUsage:
        return TokenUsage(input_tokens=len(text), truncated_tokens=0)

    def encode(
        self,
        texts: list[str],
        *,
        query: bool = False,
        batch_size: int = 4,
    ) -> tuple[np.ndarray, list[TokenUsage]]:
        self.encode_calls += 1
        if self.encode_calls == self.fail_on_call:
            raise RuntimeError("injected batch failure")
        vectors = np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(texts), 1))
        usages = [self.token_usage(text, query=query) for text in texts]
        return vectors, usages

    def metadata(self) -> dict[str, str]:
        return {"device": "cpu", "dtype": "float32"}


def test_build_vectors_resumes_three_chunks_without_losing_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages_path = tmp_path / "pages.jsonl"
    pages = [
        {
            "source_id": f"AS001:{page:03d}",
            "book_id": "AS001",
            "page": page,
            "lines": [f"line {page}"],
            "text": f"fresh page {page}",
        }
        for page in range(1, 4)
    ]
    pages_path.write_text(
        "".join(json.dumps(page) + "\n" for page in pages),
        encoding="utf-8",
    )
    units_path = tmp_path / "units.jsonl"
    units_path.write_text("", encoding="utf-8")
    vectors_dir = tmp_path / "vectors-v1"
    instances: list[_FakeEmbedder] = []

    def make_embedder(**_kwargs: object) -> _FakeEmbedder:
        fake = _FakeEmbedder(fail_on_call=2 if not instances else None)
        instances.append(fake)
        return fake

    monkeypatch.setattr(runner, "SnowflakeEmbedder", make_embedder)
    options = {
        "corpus": "A",
        "pages_path": pages_path,
        "units_path": units_path,
        "vectors_dir": vectors_dir,
        "device": "cpu",
        "threads": 1,
        "chunk_size": 1,
        "embedding_batch_size": 1,
    }
    with pytest.raises(RuntimeError, match="injected batch failure"):
        runner.build_vectors(**options)

    progress_path = vectors_dir / "A.progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["completed_batches"] == [[0, 1]]

    result = runner.build_vectors(**options)
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["status"] == "complete"
    assert progress["completed_batches"] == [[0, 1], [1, 2], [2, 3]]
    assert result["corpora"]["A"]["unit_count"] == 3
    assert result["corpora"]["A"]["unique_text_count"] == 3
    assert instances[1].encode_calls == 2
    vectors = np.load(vectors_dir / "A.vectors.npy", allow_pickle=False)
    assert vectors.shape == (3, 2)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_relation_expansion_alternates_events_by_unit_kind_and_keeps_edge_proof() -> (
    None
):
    units = [
        runner.Unit(
            "u1",
            "first event unit",
            ("AS001:001",),
            "B",
            kind="B-role",
            line_spans=({"source_id": "AS001:001", "start": 20, "end": 20},),
        ),
        runner.Unit(
            "u2",
            "second event unit",
            ("AS001:002",),
            "B",
            kind="A-role",
            line_spans=({"source_id": "AS001:002", "start": 4, "end": 4},),
        ),
        runner.Unit(
            "v1",
            "third event unit",
            ("AS001:003",),
            "B",
            kind="C-role",
            line_spans=({"source_id": "AS001:003", "start": 8, "end": 8},),
        ),
        runner.Unit(
            "v2",
            "fourth event unit",
            ("AS001:004",),
            "B",
            kind="D-role",
            line_spans=({"source_id": "AS001:004", "start": 12, "end": 12},),
        ),
    ]
    relations = {
        "code_to_events": {
            "IS01001": ["event-1"],
            "IS01002": ["event-2"],
        },
        "events": {
            "event-1": {
                "unit_ids": ["u1", "u2"],
                "relations": [
                    {
                        "code": "IS-01-001",
                        "normalized_code": "IS01001",
                        "role": "contract_reference",
                        "source_id": "AS001:001",
                        "start": 20,
                        "end": 20,
                    },
                    {
                        "code": "OTHER-01",
                        "normalized_code": "OTHER01",
                        "role": "header_anchor",
                        "source_id": "AS001:002",
                        "start": 4,
                        "end": 4,
                    },
                ],
            },
            "event-2": {
                "unit_ids": ["v1", "v2"],
                "relations": [
                    {
                        "code": "IS-01-002",
                        "normalized_code": "IS01002",
                        "role": "reservation_id",
                        "source_id": "AS001:003",
                        "start": 8,
                        "end": 8,
                    },
                    {
                        "code": "OTHER-02",
                        "normalized_code": "OTHER02",
                        "role": "header_anchor",
                        "source_id": "AS001:004",
                        "start": 12,
                        "end": 12,
                    },
                ],
            },
        },
    }
    related, status = runner.related_unit_ids(
        "lookup IS-01-001 and IS-01-002",
        ["base"],
        units,
        relations,
        {f"AS001:{page:03d}": page for page in range(1, 5)},
    )
    assert related == ["u2", "v1", "u1", "v2"]
    assert status["event_ids"] == ["event-1", "event-2"]
    assert [item["unit_id"] for item in status["expanded_relations"]] == related
    assert [item["edge_role"] for item in status["expanded_relations"]] == [
        "header_anchor",
        "reservation_id",
        "contract_reference",
        "header_anchor",
    ]


def test_ambiguous_and_more_than_two_events_leave_c_candidates_unexpanded() -> None:
    docs = [_unit("base", "ordinary candidate")]
    ambiguous = {
        "code_to_events": {"IS01001": ["event-1", "event-2"]},
        "events": {
            "event-1": {"relations": [{"code": "IS-01-001"}]},
            "event-2": {"relations": [{"code": "IS-01-001"}]},
        },
    }
    candidates, related, status = runner._relation_condition_candidates(
        "C", "IS-01-001", ["base"], docs, ambiguous, {}
    )
    assert candidates == ["base"]
    assert related == []
    assert status["status"] == "ambiguous_code"

    too_many = {
        "code_to_events": {
            "IS01001": ["event-1"],
            "IS01002": ["event-2"],
            "IS01003": ["event-3"],
        },
        "events": {
            f"event-{index}": {
                "relations": [
                    {"code": f"IS-01-00{index}", "normalized_code": f"IS0100{index}"}
                ]
            }
            for index in range(1, 4)
        },
    }
    candidates, related, status = runner._relation_condition_candidates(
        "C", "IS-01-001, then IS-01-002, plus IS-01-003", ["base"], docs, too_many, {}
    )
    assert candidates == ["base"]
    assert related == []
    assert status["status"] == "too_many_events_unsupported"


def _d_relation_fixture(
    specs: list[tuple[str, list[tuple[str, str, str]]]],
) -> tuple[list[runner.Unit], list[runner.Unit], dict[str, object], dict[str, int]]:
    pages: dict[str, runner.Unit] = {}
    records: list[runner.Unit] = []
    events: dict[str, object] = {}
    code_to_events: dict[str, list[str]] = {}
    source_order: dict[str, int] = {}
    for event_number, (event_id, rows) in enumerate(specs, 1):
        unit_ids: list[str] = []
        relations: list[dict[str, object]] = []
        for row_number, (source_id, unit_id, kind) in enumerate(rows, 1):
            source_order[source_id] = int(source_id.rsplit(":", 1)[1])
            pages[source_id] = runner.Unit(
                f"A:{source_id}:raw", f"page {source_id}", (source_id,), "A"
            )
            start = row_number * 10
            records.append(
                runner.Unit(
                    unit_id,
                    f"record {unit_id}",
                    (source_id,),
                    "B",
                    kind=kind,
                    line_spans=(
                        {"source_id": source_id, "start": start, "end": start},
                    ),
                )
            )
            unit_ids.append(unit_id)
            code = (
                f"EV-{event_number:02d}-001"
                if row_number == 1
                else f"REF-{event_number:02d}-{row_number:03d}"
            )
            normalized = runner.normalize_code(code)
            relations.append(
                {
                    "code": code,
                    "normalized_code": normalized,
                    "role": kind,
                    "source_id": source_id,
                    "start": start,
                    "end": start,
                }
            )
            if row_number == 1:
                code_to_events[normalized] = [event_id]
        events[event_id] = {"unit_ids": unit_ids, "relations": relations}
    relation_index: dict[str, object] = {
        "code_to_events": code_to_events,
        "events": events,
    }
    return list(pages.values()), records, relation_index, source_order


def _source_for(unit_id: str, docs: list[runner.Unit]) -> str:
    return next(doc.sources[0] for doc in docs if doc.unit_id == unit_id)


def test_d_projects_role_order_and_alternates_events_while_c_stays_record_based() -> (
    None
):
    pages, records, relations, order = _d_relation_fixture(
        [
            (
                "event-1",
                [
                    ("AS001:001", "record-z", "z-role"),
                    ("AS001:002", "record-a", "a-role"),
                ],
            ),
            (
                "event-2",
                [
                    ("AS001:003", "record-b", "b-role"),
                    ("AS001:004", "record-c", "c-role"),
                ],
            ),
        ]
    )
    query = "Compare EV-01-001 with EV-02-001"
    d_base = ["A:AS001:099:raw"]
    c_base = ["B:AS001:099:record"]

    _d_candidates, d_related, d_status = runner._relation_condition_candidates(
        "D", query, d_base, pages, relations, order, relation_docs=records
    )
    _c_candidates, c_related, c_status = runner._relation_condition_candidates(
        "C", query, c_base, records, relations, order
    )

    assert [_source_for(unit_id, pages) for unit_id in d_related] == [
        "AS001:002",
        "AS001:003",
        "AS001:001",
        "AS001:004",
    ]
    assert c_related == ["record-a", "record-b", "record-z", "record-c"]
    assert d_status["event_ids"] == c_status["event_ids"] == ["event-1", "event-2"]


def test_d_deduplicates_pages_before_cap_and_backfills_distinct_sources() -> None:
    rows = [
        ("AS001:001", "row-a", "a-role"),
        ("AS001:001", "row-b", "b-role"),
    ]
    rows.extend(
        (f"AS001:{page:03d}", f"row-{page}", f"{chr(97 + page)}-role")
        for page in range(2, 10)
    )
    pages, records, relations, order = _d_relation_fixture([("event-1", rows)])

    _candidates, related, status = runner._relation_condition_candidates(
        "D", "Find EV-01-001", [], pages, relations, order, relation_docs=records
    )

    assert [_source_for(unit_id, pages) for unit_id in related] == [
        f"AS001:{page:03d}" for page in range(1, 9)
    ]
    assert len(related) == runner.MAX_RELATED_UNITS
    assert len(status["expanded_relations"]) == runner.MAX_RELATED_UNITS


def test_d_skips_hybrid_page_hits_and_caps_the_rrf_merge_at_twenty() -> None:
    rows = [
        (f"AS001:{page:03d}", f"record-{page}", f"role-{page:02d}")
        for page in range(1, 10)
    ]
    pages, records, relations, order = _d_relation_fixture([("event-1", rows)])
    extra_pages = [
        runner.Unit(
            f"A:AS001:{page:03d}:raw",
            f"page AS001:{page:03d}",
            (f"AS001:{page:03d}",),
            "A",
        )
        for page in range(10, 29)
    ]
    pages.extend(extra_pages)
    base = [pages[0].unit_id, *(doc.unit_id for doc in extra_pages)]

    candidates, related, status = runner._relation_condition_candidates(
        "D", "Find EV-01-001", base, pages, relations, order, relation_docs=records
    )

    assert "AS001:001" not in {_source_for(unit_id, pages) for unit_id in related}
    assert len(related) == runner.MAX_RELATED_UNITS
    assert len(candidates) == runner.CANDIDATE_LIMIT
    assert candidates == runner.reciprocal_rank_fusion(
        [base, related], limit=runner.CANDIDATE_LIMIT
    )
    assert status["status"] == "expanded"


def test_d_ambiguous_and_over_limit_targets_keep_the_hybrid_list() -> None:
    pages, records, _relations, order = _d_relation_fixture(
        [
            (f"event-{index}", [(f"AS001:{index:03d}", f"record-{index}", "role")])
            for index in range(1, 4)
        ]
    )
    base = [pages[0].unit_id]
    ambiguous = {
        "code_to_events": {"EV01001": ["event-1", "event-2"]},
        "events": {
            f"event-{index}": {
                "unit_ids": [f"record-{index}"],
                "relations": [
                    {
                        "code": "EV-01-001",
                        "normalized_code": "EV01001",
                        "source_id": f"AS001:{index:03d}",
                        "start": 10,
                        "end": 10,
                    }
                ],
            }
            for index in (1, 2)
        },
    }

    candidates, related, status = runner._relation_condition_candidates(
        "D", "Find EV-01-001", base, pages, ambiguous, order, relation_docs=records
    )
    assert candidates == base
    assert related == []
    assert status["status"] == "ambiguous_code"

    too_many = {
        "code_to_events": {
            f"EV0{index}001": [f"event-{index}"] for index in range(1, 4)
        },
        "events": {
            f"event-{index}": {
                "unit_ids": [f"record-{index}"],
                "relations": [
                    {
                        "code": f"EV-0{index}-001",
                        "normalized_code": f"EV0{index}001",
                        "source_id": f"AS001:{index:03d}",
                        "start": 10,
                        "end": 10,
                    }
                ],
            }
            for index in range(1, 4)
        },
    }
    candidates, related, status = runner._relation_condition_candidates(
        "D",
        "Find EV-01-001, EV-02-001, and EV-03-001",
        base,
        pages,
        too_many,
        order,
        relation_docs=records,
    )
    assert candidates == base
    assert related == []
    assert status["status"] == "too_many_events_unsupported"


class _ByteEncoding:
    def encode(
        self, text: str, *, disallowed_special: tuple[str, ...] = ()
    ) -> list[int]:
        del disallowed_special
        return list(text.encode("utf-8"))

    def decode(self, tokens: list[int], *, errors: str = "replace") -> str:
        return bytes(tokens).decode("utf-8", errors=errors)


def test_context_budget_truncation_keeps_original_korean_prefix() -> None:
    page_text = "가" * 8100
    unit = runner.Unit("A:AS001:001:raw", page_text, ("AS001:001",), "A")
    final_sources, context_tokens, _digest = runner._restore_context(
        [unit.unit_id],
        {unit.unit_id: unit},
        {"AS001:001": {"text": page_text}},
        _ByteEncoding(),
    )
    actual = final_sources[0]["text"]
    assert page_text.startswith(actual)
    assert "\ufffd" not in actual
    assert actual != page_text
    assert context_tokens <= runner.MAX_CONTEXT_TOKENS


def test_vector_manifest_records_and_checksums_completed_array(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages_path = tmp_path / "pages.jsonl"
    pages_path.write_text(
        json.dumps(
            {
                "source_id": "AS001:001",
                "book_id": "AS001",
                "page": 1,
                "lines": ["one"],
                "text": "fresh page",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    units_path = tmp_path / "units.jsonl"
    units_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(runner, "SnowflakeEmbedder", lambda **_kwargs: _FakeEmbedder())
    result = runner.build_vectors(
        corpus="A",
        pages_path=pages_path,
        units_path=units_path,
        vectors_dir=tmp_path / "vectors",
        device="cpu",
        threads=1,
        chunk_size=1,
        embedding_batch_size=1,
    )
    progress = json.loads(
        (tmp_path / "vectors" / "A.progress.json").read_text(encoding="utf-8")
    )
    vector_path = tmp_path / "vectors" / "A.vectors.npy"
    assert progress["vectors_sha256"] == runner.sha256_file(vector_path)
    assert progress["vector_file_size_bytes"] == vector_path.stat().st_size
    assert result["corpora"]["A"]["vectors_sha256"] == progress["vectors_sha256"]


def test_local_snapshot_lookup_requires_the_exact_revision(tmp_path: Path) -> None:
    snapshot = tmp_path / "models--example--model" / "snapshots" / "frozen-revision"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"weights")
    from experimental.u_10o0o.fresh_record_hybrid.models import snapshot_path

    assert (
        snapshot_path("example/model", "frozen-revision", tmp_path)
        == snapshot.resolve()
    )
    with pytest.raises(FileNotFoundError, match="Required local model snapshot"):
        snapshot_path("example/model", "other-revision", tmp_path)


class _LengthTokenizer:
    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        truncation: bool = False,
        return_attention_mask: bool = False,
    ) -> dict[str, list[int]]:
        del truncation, return_attention_mask
        count = len(text) + int(add_special_tokens)
        return {"input_ids": list(range(count))}


def test_embedder_token_usage_counts_snapshot_query_prefix() -> None:
    from experimental.u_10o0o.fresh_record_hybrid.models import SnowflakeEmbedder

    embedder = SnowflakeEmbedder.__new__(SnowflakeEmbedder)
    embedder.tokenizer = _LengthTokenizer()
    embedder.query_prefix = "query: "
    usage = embedder.token_usage("q" * 2048, query=True)
    assert usage.input_tokens == len("query: " + "q" * 2048) + 1
    assert usage.truncated_tokens == 8
    assert usage.used_tokens == 2048


class _PairLengthTokenizer(_LengthTokenizer):
    def num_special_tokens_to_add(self, *, pair: bool) -> int:
        assert pair
        return 3

    def truncate_sequences(
        self,
        ids: list[int],
        *,
        pair_ids: list[int],
        num_tokens_to_remove: int,
        truncation_strategy: str,
        stride: int,
    ) -> tuple[list[int], list[int], list[int]]:
        assert truncation_strategy == "longest_first"
        assert stride == 0
        first = list(ids)
        second = list(pair_ids)
        for _ in range(num_tokens_to_remove):
            if len(first) > len(second):
                first.pop()
            else:
                second.pop()
        return first, second, []


def test_reranker_token_usage_records_pair_truncation() -> None:
    from experimental.u_10o0o.fresh_record_hybrid.models import KureReranker

    reranker = KureReranker.__new__(KureReranker)
    reranker.tokenizer = _PairLengthTokenizer()
    reranker._pair_special_tokens = 3
    reranker.max_length = 2048
    usage = reranker.pair_token_usage("q" * 3, "d" * 2045)
    assert usage.query_tokens == 3
    assert usage.query_truncated_tokens == 0
    assert usage.document_tokens == 2045
    assert usage.document_truncated_tokens == 3
    assert usage.pair_tokens == 2048
    assert usage.pair_truncated_tokens == 3


def test_e_reuses_d_new_pages_and_gives_a_hybrid_overlap_a_second_rrf_vote() -> None:
    rows = [
        (f"AS001:{page:03d}", f"record-{page:02d}", f"role-{page:02d}")
        for page in range(1, 11)
    ]
    pages, records, relations, order = _d_relation_fixture([("event-1", rows)])
    extras = [
        runner.Unit(
            f"A:AS001:{page:03d}:raw",
            f"page AS001:{page:03d}",
            (f"AS001:{page:03d}",),
            "A",
        )
        for page in range(11, 30)
    ]
    pages.extend(extras)
    overlap = "A:AS001:001:raw"
    base = [doc.unit_id for doc in extras[:19]] + [overlap]

    d_candidates, d_new, d_status = runner._relation_condition_candidates(
        "D", "Find EV-01-001", base, pages, relations, order, relation_docs=records
    )
    e_candidates, e_ranked, e_status = runner._relation_condition_candidates(
        "E", "Find EV-01-001", base, pages, relations, order, relation_docs=records
    )

    d_new_sources = {_source_for(unit_id, pages) for unit_id in d_new}
    e_new_sources = {
        _source_for(unit_id, pages) for unit_id in e_status["added_unit_ids"]
    }
    assert (
        d_new_sources == e_new_sources == {f"AS001:{page:03d}" for page in range(2, 10)}
    )
    assert d_status["expanded_relations"] == e_status["expanded_relations"]
    assert e_ranked[0] == overlap
    assert e_status["overlap_unit_ids"] == [overlap]
    assert e_ranked.count(overlap) == 1
    assert "A:AS001:010:raw" not in e_ranked
    assert overlap not in d_candidates
    assert overlap in e_candidates
    assert len(e_candidates) == runner.CANDIDATE_LIMIT
    assert e_candidates == runner.reciprocal_rank_fusion(
        [base, e_ranked], limit=runner.CANDIDATE_LIMIT
    )
    e_hybrid_rank = base.index(overlap) + 1
    e_relation_rank = e_ranked.index(overlap) + 1
    expected_score = 1 / (runner.RRF_K + e_hybrid_rank) + 1 / (
        runner.RRF_K + e_relation_rank
    )
    actual_score = 1 / (runner.RRF_K + 20) + 1 / (runner.RRF_K + 1)
    assert expected_score == actual_score
    assert expected_score > 1 / (runner.RRF_K + e_hybrid_rank)


def test_e_deduplicates_overlaps_across_edges_and_events_and_round_robins() -> None:
    event_one = [
        ("AS001:001", "record-a", "role-01"),
        ("AS001:001", "record-b", "role-01b"),
        *[
            (f"AS001:{page:03d}", f"one-{page}", f"role-{page:02d}")
            for page in range(2, 11)
        ],
    ]
    event_two = [
        ("AS001:001", "record-c", "role-01c"),
        *[
            (f"AS001:{page:03d}", f"two-{page}", f"role-{page:02d}")
            for page in range(11, 20)
        ],
    ]
    pages, records, relations, order = _d_relation_fixture(
        [("event-1", event_one), ("event-2", event_two)]
    )
    overlap = "A:AS001:001:raw"
    base = [overlap]

    _fused, d_new, d_status = runner._relation_condition_candidates(
        "D",
        "Compare EV-01-001 with EV-02-001",
        base,
        pages,
        relations,
        order,
        relation_docs=records,
    )
    _fused, e_ranked, e_status = runner._relation_condition_candidates(
        "E",
        "Compare EV-01-001 with EV-02-001",
        base,
        pages,
        relations,
        order,
        relation_docs=records,
    )

    assert e_status["event_ids"] == d_status["event_ids"] == ["event-1", "event-2"]
    assert e_status["added_unit_ids"] == d_new
    assert e_ranked.count(overlap) == 1
    assert len(e_ranked) == len(set(e_ranked))
    proofs = {proof["unit_id"]: proof for proof in e_status["ranking_relations"]}
    assert proofs[overlap]["event_id"] == "event-1"
    assert "A:AS001:010:raw" not in e_ranked
    assert len(e_status["overlap_unit_ids"]) == 1


def test_e_falls_back_to_the_same_base_candidates_for_ambiguous_targets() -> None:
    pages, records, relations, order = _d_relation_fixture(
        [
            (f"event-{index}", [(f"AS001:{index:03d}", f"record-{index}", "role")])
            for index in range(1, 3)
        ]
    )
    relations["code_to_events"]["EV01001"] = ["event-1", "event-2"]
    base = [doc.unit_id for doc in pages]

    d_candidates, d_new, d_status = runner._relation_condition_candidates(
        "D", "Find EV-01-001", base, pages, relations, order, relation_docs=records
    )
    e_candidates, e_related, e_status = runner._relation_condition_candidates(
        "E", "Find EV-01-001", base, pages, relations, order, relation_docs=records
    )

    assert e_candidates == d_candidates == base
    assert e_related == d_new == []
    assert e_status["status"] == d_status["status"] == "ambiguous_code"
    assert e_status["event_ids"] == d_status["event_ids"] == []
