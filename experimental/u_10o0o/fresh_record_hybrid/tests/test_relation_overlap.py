from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from experimental.u_10o0o.fresh_record_hybrid import relation_overlap as replay
from experimental.u_10o0o.fresh_record_hybrid import retrieval as r


def _source_fixture(tmp_path: Path):
    question = r.Question("q-1", "Find EV-01-001")
    pages = [
        r.Unit(
            f"A:AS001:{page:03d}:raw",
            f"original page {page}",
            (f"AS001:{page:03d}",),
            "A",
            kind="raw_page",
            line_spans=(
                {
                    "source_id": f"AS001:{page:03d}",
                    "start": 1,
                    "end": 1,
                },
            ),
        )
        for page in range(1, 22)
    ]
    records = [
        r.Unit(
            f"B:record-{page}",
            f"verified record {page}",
            (f"AS001:{page:03d}",),
            "B",
            kind=f"role-{page}",
            line_spans=(
                {
                    "source_id": f"AS001:{page:03d}",
                    "start": 1,
                    "end": 1,
                },
            ),
        )
        for page in (1, 2)
    ]
    relation_index = {
        "code_to_events": {"EV01001": ["event-1"]},
        "events": {
            "event-1": {
                "unit_ids": [record.unit_id for record in records],
                "relations": [
                    {
                        "code": "EV-01-001",
                        "normalized_code": "EV01001",
                        "role": record.kind,
                        "source_id": record.sources[0],
                        "start": 1,
                        "end": 1,
                    }
                    for record in records
                ],
            }
        },
    }
    page_path = tmp_path / "pages.jsonl"
    unit_path = tmp_path / "units.jsonl"
    relation_path = tmp_path / "relations.json"
    question_path = tmp_path / "questions.jsonl"
    page_path.write_text(
        "".join(
            json.dumps(
                {
                    "source_id": page.sources[0],
                    "book_id": "AS001",
                    "page": int(page.sources[0].split(":")[1]),
                    "text": page.text,
                    "lines": [page.text],
                }
            )
            + "\n"
            for page in pages
        )
    )
    unit_path.write_text(
        "".join(
            json.dumps(
                {
                    "unit_id": unit.unit_id,
                    "corpus": "B",
                    "text": unit.text,
                    "sources": list(unit.sources),
                    "kind": unit.kind,
                    "line_spans": list(unit.line_spans),
                }
            )
            + "\n"
            for unit in records
        )
    )
    relation_path.write_text(json.dumps(relation_index))
    question_path.write_text(
        json.dumps({"id": question.question_id, "question": question.text}) + "\n"
    )
    loaded_pages, page_map, loaded_order = r.load_pages(page_path)
    loaded_records = r.load_record_units(unit_path)
    loaded_relations = json.loads(relation_path.read_text())
    loaded_questions = r.load_questions(question_path)
    fingerprints = r._input_fingerprints(
        page_path, unit_path, relation_path, loaded_questions
    )
    patterns = r._code_patterns(loaded_relations)
    base = ["A:AS001:003:raw", "A:AS001:001:raw"] + [
        f"A:AS001:{page:03d}:raw" for page in range(4, 22)
    ]
    d_candidates, d_new, d_metadata = r._relation_condition_candidates(
        "D",
        question.text,
        base,
        loaded_pages,
        loaded_relations,
        loaded_order,
        patterns,
        relation_docs=loaded_records,
    )
    assert d_new == ["A:AS001:002:raw"]
    saved = {
        "id": question.question_id,
        "condition": "D",
        "question_sha256": r.sha256_text(question.text),
        "hybrid_unit_ids": base,
        "bm25_top50": base + ["A:AS001:002:raw"],
        "dense_top50": base + ["A:AS001:002:raw"],
        "related_unit_ids": d_new,
        "candidate_unit_ids": d_candidates,
        "relation": d_metadata,
        "query_embedding": {
            "input_tokens": 5,
            "truncated_tokens": 0,
            "used_tokens": 5,
            "prefix": "query: ",
        },
    }
    return {
        "question": question,
        "questions": loaded_questions,
        "pages": loaded_pages,
        "page_map": page_map,
        "records": loaded_records,
        "relations": loaded_relations,
        "order": loaded_order,
        "patterns": patterns,
        "saved": saved,
        "fingerprints": fingerprints,
        "page_path": page_path,
        "unit_path": unit_path,
        "relation_path": relation_path,
        "question_path": question_path,
    }


def _source_manifest(fixture):
    return {
        "conditions": ["D"],
        "summary": {"result_rows": 1},
        "inputs": fixture["fingerprints"],
        "bm25": {
            "k1": 1.5,
            "b": 0.75,
            "tokenizer": "Korean words+bigram+complete code",
        },
        "dense": {"metric": "exact cosine", "top_k": 50},
        "sparse_top_k": 50,
        "rrf_k": r.RRF_K,
        "candidate_limit": r.CANDIDATE_LIMIT,
        "max_related_events": r.MAX_RELATED_EVENTS,
        "max_related_units": r.MAX_RELATED_UNITS,
        "rerank_batch_size": 8,
        "max_model_tokens": r.MAX_MODEL_TOKENS,
        "context": {
            "tokenizer": "o200k_base",
            "max_sources": r.MAX_FINAL_SOURCES,
            "max_tokens": r.MAX_CONTEXT_TOKENS,
        },
    }


def test_validate_d_run_rejects_incomplete_rows_and_changed_fingerprints(
    tmp_path: Path,
) -> None:
    fixture = _source_fixture(tmp_path)
    manifest = _source_manifest(fixture)
    saved = fixture["saved"]
    with pytest.raises(ValueError, match="exactly one row"):
        replay.validate_d_run(
            [],
            fixture["questions"],
            manifest,
            fixture["fingerprints"],
        )
    changed = {**fixture["fingerprints"], "pages_file_sha256": "changed"}
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        replay.validate_d_run(
            [saved],
            fixture["questions"],
            manifest,
            changed,
        )


def test_replay_candidates_freezes_d_new_pages_and_adds_only_hybrid_overlap(
    tmp_path: Path,
) -> None:
    fixture = _source_fixture(tmp_path)
    state = replay.replay_candidates(
        fixture["question"].text,
        fixture["saved"],
        fixture["pages"],
        fixture["records"],
        fixture["relations"],
        fixture["order"],
        fixture["patterns"],
    )

    assert state.condition == "E"
    assert state.related == ["A:AS001:001:raw", "A:AS001:002:raw"]
    assert state.relation["overlap_unit_ids"] == ["A:AS001:001:raw"]
    assert state.relation["added_unit_ids"] == ["A:AS001:002:raw"]
    assert set(state.candidates) == set(fixture["saved"]["candidate_unit_ids"])
    assert state.candidates != fixture["saved"]["candidate_unit_ids"]
    assert state.sparse_top == fixture["saved"]["bm25_top50"]
    assert state.dense_top == fixture["saved"]["dense_top50"]


def test_replay_candidates_rejects_a_d_result_with_changed_candidate_chain(
    tmp_path: Path,
) -> None:
    fixture = _source_fixture(tmp_path)
    poisoned = {**fixture["saved"], "candidate_unit_ids": ["A:AS001:001:raw"]}
    with pytest.raises(ValueError, match="D relation replay mismatch"):
        replay.replay_candidates(
            fixture["question"].text,
            poisoned,
            fixture["pages"],
            fixture["records"],
            fixture["relations"],
            fixture["order"],
            fixture["patterns"],
        )


def test_replay_runs_one_fake_rerank_and_restores_original_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _source_fixture(tmp_path)
    d_dir = tmp_path / "D"
    d_dir.mkdir()
    d_results = d_dir / "results.jsonl"
    d_results.write_text(json.dumps(fixture["saved"]) + "\n")
    source_manifest = _source_manifest(fixture)
    source_manifest["code_sha256"] = {
        "models.py": r.sha256_file(Path(r.__file__).with_name("models.py"))
    }
    source_manifest["reranker_model"] = {"model_id": "fake-local-reranker"}
    (d_dir / "run-manifest.json").write_text(json.dumps(source_manifest))

    class FakeReranker:
        def __init__(self, **kwargs):
            del kwargs

        def metadata(self):
            return {"model_id": "fake-local-reranker"}

        def score_pairs(self, pairs, *, batch_size):
            assert batch_size == 8
            usages = [
                SimpleNamespace(
                    pair_tokens=4,
                    truncated=False,
                    query_tokens=2,
                    query_truncated_tokens=0,
                    document_tokens=2,
                    document_truncated_tokens=0,
                )
                for _ in pairs
            ]
            return np.arange(len(pairs), dtype=np.float32), usages

    monkeypatch.setattr(replay, "KureReranker", FakeReranker)
    output = tmp_path / "E" / "results.jsonl"
    summary = replay.replay(
        d_results=d_results,
        results=output,
        d_results_sha256=r.sha256_file(d_results),
        pages_path=fixture["page_path"],
        units_path=fixture["unit_path"],
        relations_path=fixture["relation_path"],
        questions_path=fixture["question_path"],
        device="cpu",
    )

    row = json.loads(output.read_text().strip())
    assert row["condition"] == "E"
    assert row["initial_rankings_reused"] is True
    assert row["query_embedding_reused"] is True
    assert row["query_embedding"] == fixture["saved"]["query_embedding"]
    assert row["new_related_unit_ids"] == fixture["saved"]["related_unit_ids"]
    assert row["relation"]["overlap_unit_ids"] == ["A:AS001:001:raw"]
    assert [source["source_id"] for source in row["final_sources"]]
    assert summary["query_embedding_calls"] == 0
    assert summary["document_embedding_calls"] == 0
    assert summary["bm25_calls"] == 0
    assert summary["dense_search_calls"] == 0
    assert summary["paid_API_calls"] == 0

    partial = output.with_suffix(output.suffix + ".partial")
    partial.touch()
    with pytest.raises(FileExistsError, match="Choose a new result path"):
        replay.replay(d_results=tmp_path / "missing-D-results", results=output)


def test_replay_preserves_an_existing_manifest_even_with_a_new_result_name(tmp_path):
    previous = tmp_path / "run-manifest.json"
    previous.write_text('{"existing": true}')
    with pytest.raises(FileExistsError, match="Choose a new result path"):
        replay.replay(
            d_results=tmp_path / "not-opened",
            results=tmp_path / "another-name.jsonl",
        )
    assert previous.read_text() == '{"existing": true}'
    assert not (tmp_path / "another-name.jsonl").exists()


@pytest.mark.parametrize(
    "name", ["run-manifest.json", "run-manifest.json.tmp", "results.jsonl.partial"]
)
def test_replay_rejects_reserved_output_paths_before_opening_inputs(tmp_path, name):
    with pytest.raises(ValueError, match="must use a .jsonl"):
        replay.replay(d_results=tmp_path / "not-opened", results=tmp_path / name)
    assert list(tmp_path.iterdir()) == []


def test_replay_rejects_a_modified_frozen_d_file_before_manifest_loading(tmp_path):
    saved = tmp_path / "D.jsonl"
    saved.write_text('{"frozen": true}')
    expected = r.sha256_file(saved)
    saved.write_text('{"frozen": false}')
    with pytest.raises(ValueError, match="checksum mismatch"):
        replay.replay(
            d_results=saved,
            results=tmp_path / "E.jsonl",
            d_results_sha256=expected,
        )


@pytest.mark.parametrize(
    "key,value",
    [("sparse_top_k", 20), ("dense", {"metric": "exact cosine", "top_k": 20})],
)
def test_replay_rejects_nonfrozen_search_budget(tmp_path, key, value):
    fixture = _source_fixture(tmp_path)
    manifest = {**_source_manifest(fixture), key: value}
    with pytest.raises(ValueError, match="unsupported settings"):
        replay.validate_d_run(
            [fixture["saved"]], fixture["questions"], manifest, fixture["fingerprints"]
        )


def test_replay_rejects_short_or_inconsistent_saved_hybrid(tmp_path):
    fixture = _source_fixture(tmp_path)
    for base, message in [
        (fixture["saved"]["hybrid_unit_ids"][:-1], "Invalid frozen D hybrid"),
        (list(reversed(fixture["saved"]["hybrid_unit_ids"])), "saved RRF inputs"),
    ]:
        modified = {**fixture["saved"], "hybrid_unit_ids": base}
        with pytest.raises(ValueError, match=message):
            replay.replay_candidates(
                fixture["question"].text,
                modified,
                fixture["pages"],
                fixture["records"],
                fixture["relations"],
                fixture["order"],
                fixture["patterns"],
            )


def _ranked_f_relation_fixture():
    question = r.Question("f-q", "Find EV-01-001")
    pages = [
        r.Unit(
            f"A:AS001:{page:03d}:raw",
            f"page {page}",
            (f"AS001:{page:03d}",),
            "A",
            kind="raw_page",
            line_spans=({"source_id": f"AS001:{page:03d}", "start": 1, "end": 1},),
        )
        for page in range(1, 61)
    ]
    records = [
        r.Unit(
            f"B:record-{page}",
            f"record {page}",
            (f"AS001:{page:03d}",),
            "B",
            kind=f"role-{page}",
            line_spans=({"source_id": f"AS001:{page:03d}", "start": 1, "end": 1},),
        )
        for page in (1, 2)
    ]
    relations = {
        "code_to_events": {"EV01001": ["event-1"]},
        "events": {
            "event-1": {
                "unit_ids": [unit.unit_id for unit in records],
                "relations": [
                    {
                        "code": "EV-01-001",
                        "normalized_code": "EV01001",
                        "role": unit.kind,
                        "source_id": unit.sources[0],
                        "start": 1,
                        "end": 1,
                    }
                    for unit in records
                ],
            }
        },
    }
    pages_by_number = {int(page.sources[0].split(":")[1]): page for page in pages}
    sparse = [pages_by_number[number].unit_id for number in range(1, 51)]
    dense = [
        pages_by_number[number].unit_id for number in [*range(11, 31), *range(31, 61)]
    ]
    patterns = r._code_patterns(relations)
    source_order = {page.sources[0]: i for i, page in enumerate(pages)}
    base = r.reciprocal_rank_fusion([sparse, dense], limit=20)
    d_candidates, d_new, d_meta = r._relation_condition_candidates(
        "D",
        question.text,
        base,
        pages,
        relations,
        source_order,
        patterns,
        relation_docs=records,
    )
    saved = {
        "id": question.question_id,
        "condition": "D",
        "question_sha256": r.sha256_text(question.text),
        "bm25_top50": sparse,
        "dense_top50": dense,
        "hybrid_unit_ids": base,
        "candidate_unit_ids": d_candidates,
        "related_unit_ids": d_new,
        "relation": d_meta,
    }
    return question, pages, records, relations, source_order, patterns, saved


def test_quota_candidates_fills_overlap_slots_from_original_rrf_order() -> None:
    sparse = ["shared-" + str(i) for i in range(10)] + [
        "sparse-" + str(i) for i in range(40)
    ]
    dense = ["shared-" + str(i) for i in range(10)] + [
        "dense-" + str(i) for i in range(40)
    ]
    full_rrf = r.reciprocal_rank_fusion([sparse, dense], limit=100)

    selected = replay.quota_candidates(sparse, dense)
    quota = set(sparse[:10]) | set(dense[:10])
    expected_set = quota | {unit_id for unit_id in full_rrf if unit_id not in quota}
    expected = [unit_id for unit_id in full_rrf if unit_id in expected_set][:20]

    assert len(quota) == 10
    assert len(selected) == 20
    assert len(set(selected)) == 20
    assert selected == expected
    assert set(selected) >= quota
    assert len(set(selected) - quota) == 10


def test_f_quota_keeps_strong_single_retriever_result_excluded_from_old_rrf20() -> None:
    (
        _question,
        _pages,
        _records,
        _relations,
        _source_order,
        _patterns,
        saved,
    ) = _ranked_f_relation_fixture()
    sparse = saved["bm25_top50"]
    dense = saved["dense_top50"]
    target = sparse[1]
    full_rrf = r.reciprocal_rank_fusion([sparse, dense], limit=100)
    quota = set(sparse[:10]) | set(dense[:10])
    selected = replay.quota_candidates(sparse, dense)

    assert target not in dense
    assert target not in saved["hybrid_unit_ids"]
    assert target in quota
    assert len(selected) == 20
    assert quota <= set(selected)
    assert set(selected) == quota
    assert selected == [unit_id for unit_id in full_rrf if unit_id in set(selected)]


def test_f_replays_e_relation_policy_against_its_quota_base() -> None:
    question, pages, records, relations, source_order, patterns, saved = (
        _ranked_f_relation_fixture()
    )
    target_pages = {
        pages[0].unit_id,
        pages[1].unit_id,
    }
    assert target_pages.isdisjoint(saved["hybrid_unit_ids"])
    assert set(saved["related_unit_ids"]) == target_pages

    e_state = replay.replay_candidates(
        question.text, saved, pages, records, relations, source_order, patterns
    )
    f_state = replay.replay_candidates(
        question.text,
        saved,
        pages,
        records,
        relations,
        source_order,
        patterns,
        condition="F",
    )

    assert e_state.condition == "E"
    assert e_state.hybrid == saved["hybrid_unit_ids"]
    assert set(e_state.relation["added_unit_ids"]) == target_pages
    assert f_state.condition == "F"
    assert f_state.hybrid == replay.quota_candidates(
        saved["bm25_top50"], saved["dense_top50"]
    )
    assert len(f_state.hybrid) == 20
    assert target_pages <= set(f_state.hybrid)
    assert f_state.relation["event_ids"] == e_state.relation["event_ids"]
    assert f_state.relation["added_unit_ids"] == []
    assert set(f_state.relation["overlap_unit_ids"]) == target_pages
    assert f_state.related == e_state.related
    assert len(f_state.candidates) == 20
