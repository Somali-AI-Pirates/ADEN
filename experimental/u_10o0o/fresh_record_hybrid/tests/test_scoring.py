import hashlib
import json

import pytest

from experimental.u_10o0o.fresh_record_hybrid.scoring import (
    fact_present,
    rank_metrics,
    score_one,
    score_run,
)


def test_quote_required_and_book_identity():
    fact = {"alternatives": [{"source_id": "AS001:001", "quote": "실제 반출 12월 3일"}]}
    assert not fact_present(fact, {"AS002:001": "실제 반출 12월 3일"})
    assert not fact_present(fact, {"AS001:001": "예정 반출 12월 3일"})
    assert fact_present(fact, {"AS001:001": "실제  반출\n12월 3일"})
    assert not fact_present(
        {"alternatives": [{"source_id": "AS001:001", "quote": ""}]}, {"AS001:001": "x"}
    )


def test_allowed_alternative_distinct_from_primary_page_recall():
    fact = {
        "alternatives": [
            {"source_id": "AS001:001", "quote": "확인"},
            {"source_id": "AS003:004", "quote": "확인"},
        ]
    }
    assert fact_present(fact, {"AS003:004": "확인"})
    assert rank_metrics(["AS003:004"], [fact])["page_recall"] == 0
    assert rank_metrics(["AS003:004"], [fact])["mrr"] == 1


def test_actual_saved_context_controls_final_hit():
    ref = {
        "split": "dev",
        "category": "status",
        "required": [
            {"alternatives": [{"source_id": "AS001:001", "quote": "미결 USD 30"}]}
        ],
    }
    row = {
        "id": "x",
        "condition": "A",
        "candidate_source_ids": ["AS001:001"],
        "final_sources": [{"source_id": "AS001:001", "text": "상단 내용"}],
    }
    result = score_one(row, ref, {"AS001:001": "상단 내용 미결 USD 30"})
    assert result["candidate_complete"]
    assert not result["final_complete"]


def test_incomplete_run_does_not_read_references(tmp_path):
    q = tmp_path / "questions.jsonl"
    q.write_text(json.dumps({"id": "x", "question": "질문"}))
    r = tmp_path / "results.jsonl"
    r.write_text(json.dumps({"id": "x", "condition": "A"}))
    with pytest.raises(ValueError, match="All three"):
        score_run(
            r,
            q,
            tmp_path / "nonexistent-gold",
            tmp_path / "none",
            tmp_path / "out",
            tmp_path / "units",
        )


def test_ambiguous_not_counted_as_vacuous_success():
    row = {
        "id": "x",
        "condition": "C",
        "candidate_source_ids": [],
        "final_sources": [],
        "relation": {"event_ids": ["wrong"]},
    }
    ref = {
        "split": "dev",
        "category": "ambiguous",
        "ambiguous": True,
        "required": [],
        "expected_relation_events": [],
    }
    result = score_one(row, ref, {})
    assert result["final_complete"] is None
    assert result["candidate_complete"] is None
    assert result["relation_wrong_count"] == 1


def test_three_arm_gate_and_shareable_report(tmp_path):
    from experimental.u_10o0o.fresh_record_hybrid.reporting import render

    def write(name, rows):
        path = tmp_path / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    question = write("questions.jsonl", [{"id": "new-01", "question": "private query"}])
    pages = write(
        "pages.jsonl", [{"source_id": "AS001:001", "text": "private source confirmed"}]
    )
    refs = write(
        "references.jsonl",
        [
            {
                "id": "new-01",
                "split": "validation",
                "category": "exact",
                "required": [
                    {
                        "fact": "private meaning",
                        "alternatives": [
                            {"source_id": "AS001:001", "quote": "confirmed"}
                        ],
                    }
                ],
            }
        ],
    )
    results = write(
        "results.jsonl",
        [
            {
                "id": "new-01",
                "condition": arm,
                "question_sha256": hashlib.sha256(b"private query").hexdigest(),
                "candidate_source_ids": ["AS001:001"],
                "candidate_unit_ids": ["unitA" if arm == "A" else "unitB"],
                "reranked_unit_ids": ["unitA" if arm == "A" else "unitB"],
                "final_sources": [
                    {"source_id": "AS001:001", "text": "private source confirmed"}
                ],
                "context_tokens": 3,
            }
            for arm in ["A", "B", "C"]
        ],
    )
    score_dir = tmp_path / "score"
    units = write(
        "units.jsonl",
        [
            {"unit_id": "unit" + arm, "corpus": arm, "sources": ["AS001:001"]}
            for arm in ["A", "B"]
        ],
    )
    summary = score_run(results, question, refs, pages, score_dir, units)
    assert all(r["final_complete"] == 1 for r in summary)
    report_dir = tmp_path / "report"
    render(score_dir, report_dir)
    all_text = "\n".join(p.read_text() for p in report_dir.glob("*"))
    assert "private query" not in all_text
    assert "private source" not in all_text
    assert "private meaning" not in all_text
    invalid_rows = [json.loads(line) for line in results.read_text().splitlines()]
    invalid_rows[0]["final_sources"][0]["source_id"] = "AS002:001"
    results.write_text("".join(json.dumps(row) + "\n" for row in invalid_rows))
    with pytest.raises(ValueError, match="selection chain"):
        score_run(results, question, refs, pages, score_dir, units)


def test_partial_checkpoint_rejects_replaced_normalized_vector(tmp_path, monkeypatch):
    import numpy as np

    from experimental.u_10o0o.fresh_record_hybrid import retrieval as runner
    from experimental.u_10o0o.fresh_record_hybrid.tests.test_retrieval import (
        _FakeEmbedder,
    )

    pages = tmp_path / "pages.jsonl"
    pages.write_text(
        "".join(
            json.dumps(
                {
                    "source_id": f"AS001:{i:03d}",
                    "book_id": "AS001",
                    "page": i,
                    "text": f"page {i}",
                    "lines": [f"page {i}"],
                }
            )
            + "\n"
            for i in range(1, 4)
        )
    )
    options = dict(
        corpus="A",
        pages_path=pages,
        units_path=tmp_path / "unused",
        vectors_dir=tmp_path / "vectors",
        device="cpu",
        threads=1,
        chunk_size=1,
        embedding_batch_size=1,
    )
    monkeypatch.setattr(
        runner, "SnowflakeEmbedder", lambda **kwargs: _FakeEmbedder(fail_on_call=2)
    )
    with pytest.raises(RuntimeError, match="injected"):
        runner.build_vectors(**options)
    matrix = np.load(tmp_path / "vectors" / "A.vectors.npy", mmap_mode="r+")
    matrix[0] = [0.0, 1.0]
    matrix.flush()
    monkeypatch.setattr(runner, "SnowflakeEmbedder", lambda **kwargs: _FakeEmbedder())
    with pytest.raises(ValueError, match="batch checksum"):
        runner.build_vectors(**options)


def test_d_page_relation_arm_and_comparison_pairs(tmp_path):
    def write(name, rows):
        path = tmp_path / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    question = write("questions.jsonl", [{"id": "d-01", "question": "device code"}])
    question_hash = hashlib.sha256(b"device code").hexdigest()
    page_text = "reservation code and original record confirmed"
    pages = write("pages.jsonl", [{"source_id": "AS001:001", "text": page_text}])
    refs = write(
        "references.jsonl",
        [
            {
                "id": "d-01",
                "split": "validation",
                "category": "explicit_code",
                "required": [
                    {
                        "alternatives": [
                            {"source_id": "AS001:001", "quote": "original record"}
                        ]
                    }
                ],
                "expected_relation_events": ["event-1"],
                "relation_eligible": True,
            }
        ],
    )
    units = write(
        "units.jsonl",
        [
            {
                "unit_id": "A:AS001:001:raw",
                "corpus": "A",
                "sources": ["AS001:001"],
            },
            {
                "unit_id": "B:AS001:001:record",
                "corpus": "B",
                "sources": ["AS001:001"],
            },
        ],
    )
    import tiktoken

    tokens = len(tiktoken.get_encoding("o200k_base").encode(page_text))
    base_result = {
        "id": "d-01",
        "question_sha256": question_hash,
        "candidate_unit_ids": ["A:AS001:001:raw"],
        "candidate_source_ids": ["AS001:001"],
        "reranked_unit_ids": ["A:AS001:001:raw"],
        "final_sources": [{"source_id": "AS001:001", "text": page_text}],
        "context_tokens": tokens,
        "relation": {"status": "expanded", "event_ids": ["event-1"]},
    }
    results = write(
        "results.jsonl",
        [{**base_result, "condition": arm} for arm in ("A", "D")],
    )
    score_dir = tmp_path / "score"
    score_run(
        results,
        question,
        refs,
        pages,
        score_dir,
        units,
        conditions=("A", "D"),
    )
    scores = json.loads((score_dir / "scores.json").read_text())
    d_score = next(row for row in scores if row["condition"] == "D")
    assert d_score["candidate_complete"]
    assert d_score["final_complete"]
    assert d_score["relation_exact"]
    manifest = json.loads((score_dir / "score-manifest.json").read_text())
    assert manifest["conditions"] == ["A", "D"]
    changes = json.loads((score_dir / "changes.json").read_text())
    assert set(changes) == {"A_D"}

    full_rows = []
    for arm in ("A", "B", "C", "D"):
        unit_id = "A:AS001:001:raw" if arm in {"A", "D"} else "B:AS001:001:record"
        full_rows.append(
            {
                **base_result,
                "condition": arm,
                "candidate_unit_ids": [unit_id],
                "reranked_unit_ids": [unit_id],
            }
        )
    full_results = write("results-abcd.jsonl", full_rows)
    full_dir = tmp_path / "score-abcd"
    score_run(
        full_results,
        question,
        refs,
        pages,
        full_dir,
        units,
        conditions=("A", "B", "C", "D"),
    )
    full_changes = json.loads((full_dir / "changes.json").read_text())
    assert set(full_changes) == {"A_B", "B_C", "A_C", "A_D", "D_C"}

    d_only = write("d-only.jsonl", [{**base_result, "condition": "D"}])
    d_only_dir = tmp_path / "score-d-only"
    score_run(
        d_only,
        question,
        refs,
        pages,
        d_only_dir,
        units,
        conditions=("D",),
    )
    only_manifest = json.loads((d_only_dir / "score-manifest.json").read_text())
    assert only_manifest["conditions"] == ["D"]
    assert json.loads((d_only_dir / "changes.json").read_text()) == {}


def test_d_only_incomplete_gate_precedes_reference_read(tmp_path):
    question = tmp_path / "questions.jsonl"
    question.write_text(json.dumps({"id": "x", "question": "질문"}))
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({"id": "x", "condition": "A"}))
    with pytest.raises(ValueError, match="All requested D"):
        score_run(
            results,
            question,
            tmp_path / "nonexistent-gold",
            tmp_path / "none",
            tmp_path / "out",
            tmp_path / "units",
            conditions=("D",),
        )


def test_d_e_preserves_per_fact_swaps_when_total_hits_match(tmp_path):
    def write(name, rows):
        path = tmp_path / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    question = write("e-questions.jsonl", [{"id": "e-01", "question": "compare"}])
    question_hash = hashlib.sha256(b"compare").hexdigest()
    pages = write(
        "e-pages.jsonl",
        [
            {"source_id": "AS001:001", "text": "invoice has amount 24"},
            {"source_id": "AS001:002", "text": "receipt has amount 36"},
        ],
    )
    refs = write(
        "e-references.jsonl",
        [
            {
                "id": "e-01",
                "split": "validation",
                "category": "two_records",
                "required": [
                    {
                        "alternatives": [
                            {"source_id": "AS001:001", "quote": "amount 24"}
                        ]
                    },
                    {
                        "alternatives": [
                            {"source_id": "AS001:002", "quote": "amount 36"}
                        ]
                    },
                ],
            }
        ],
    )
    units = write(
        "e-units.jsonl",
        [
            {
                "unit_id": f"A:{source}:raw",
                "corpus": "A",
                "sources": [source],
            }
            for source in ("AS001:001", "AS001:002")
        ],
    )
    import tiktoken

    encoding = tiktoken.get_encoding("o200k_base")
    rows = []
    for condition, page in (("D", "AS001:001"), ("E", "AS001:002"), ("F", "AS001:001")):
        text = (
            "invoice has amount 24" if page == "AS001:001" else "receipt has amount 36"
        )
        unit_id = f"A:{page}:raw"
        rows.append(
            {
                "id": "e-01",
                "condition": condition,
                "question_sha256": question_hash,
                "candidate_unit_ids": [unit_id],
                "candidate_source_ids": [page],
                "reranked_unit_ids": [unit_id],
                "final_sources": [{"source_id": page, "text": text}],
                "context_tokens": len(encoding.encode(text)),
                "relation": {"status": "expanded", "event_ids": ["event-1"]},
            }
        )
    results = write("e-results.jsonl", rows)
    score_dir = tmp_path / "e-score"
    score_run(
        results,
        question,
        refs,
        pages,
        score_dir,
        units,
        conditions=("D", "E", "F"),
    )

    scores = json.loads((score_dir / "scores.json").read_text())
    by_condition = {row["condition"]: row for row in scores}
    assert by_condition["D"]["candidate_fact_hits"] == 1
    assert by_condition["E"]["candidate_fact_hits"] == 1
    assert by_condition["D"]["candidate_fact_mask"] == [True, False]
    assert by_condition["E"]["candidate_fact_mask"] == [False, True]

    changes = json.loads((score_dir / "changes.json").read_text())["D_E"][0]
    expected_swap = {
        "gained_fact_indices": [2],
        "lost_fact_indices": [1],
        "gained_fact_count": 1,
        "lost_fact_count": 1,
    }
    assert changes["candidate_fact_changes"] == expected_swap
    assert changes["final_fact_changes"] == expected_swap
    assert changes["candidate_before"] == changes["candidate_after"] is False
    assert changes["final_before"] == changes["final_after"] is False

    manifest = json.loads((score_dir / "score-manifest.json").read_text())
    assert manifest["conditions"] == ["D", "E", "F"]
    paired = json.loads((score_dir / "changes.json").read_text())
    assert set(paired) == {"D_E", "E_F"}
    reverse = paired["E_F"][0]
    assert reverse["final_fact_changes"] == {
        "gained_fact_indices": [1],
        "lost_fact_indices": [2],
        "gained_fact_count": 1,
        "lost_fact_count": 1,
    }

    wrong_rows = [dict(row) for row in rows]
    wrong_rows[2]["candidate_unit_ids"] = ["B:AS001:002:record"]
    wrong_rows[2]["reranked_unit_ids"] = ["B:AS001:002:record"]
    wrong_results = write("e-wrong-corpus-results.jsonl", wrong_rows[2:])
    with pytest.raises(ValueError, match="requested corpus"):
        score_run(
            wrong_results,
            question,
            tmp_path / "not-opened-gold",
            pages,
            tmp_path / "wrong-corpus-score",
            units,
            conditions=("F",),
        )


def test_e_only_incomplete_gate_precedes_reference_read(tmp_path):
    question = tmp_path / "questions.jsonl"
    question.write_text(json.dumps({"id": "x", "question": "질문"}))
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({"id": "x", "condition": "D"}))
    with pytest.raises(ValueError, match="All requested E"):
        score_run(
            results,
            question,
            tmp_path / "nonexistent-gold",
            tmp_path / "none",
            tmp_path / "out",
            tmp_path / "units",
            conditions=("E",),
        )


@pytest.mark.parametrize("conditions", [("F",), ("E", "F")])
def test_f_scoring_gate_precedes_reference_read(tmp_path, conditions):
    question = tmp_path / "questions.jsonl"
    question.write_text(json.dumps({"id": "x", "question": "질문"}))
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps({"id": "x", "condition": "E"}))
    with pytest.raises(ValueError, match="All requested"):
        score_run(
            results,
            question,
            tmp_path / "nonexistent-gold",
            tmp_path / "none",
            tmp_path / "out",
            tmp_path / "units",
            conditions=conditions,
        )


def test_coverage_selection_labels_compare_final_fact_masks(tmp_path):
    def write(name, rows):
        path = tmp_path / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    question = write(
        "coverage-questions.jsonl",
        [{"id": "coverage-01", "question": "compare"}],
    )
    question_hash = hashlib.sha256(b"compare").hexdigest()
    page_texts = {
        "AS001:001": "invoice has amount 24",
        **{f"AS001:{index:03d}": f"unrelated page {index}" for index in range(2, 21)},
    }
    pages = write(
        "coverage-pages.jsonl",
        [
            {"source_id": source_id, "text": text}
            for source_id, text in page_texts.items()
        ],
    )
    refs = write(
        "coverage-references.jsonl",
        [
            {
                "id": "coverage-01",
                "split": "validation",
                "category": "one_record",
                "required": [
                    {"alternatives": [{"source_id": "AS001:001", "quote": "amount 24"}]}
                ],
                "expected_relation_events": ["event-1"],
                "relation_eligible": True,
            }
        ],
    )
    candidate_units = [f"A:{source_id}:raw" for source_id in page_texts]
    units = write(
        "coverage-units.jsonl",
        [
            {"unit_id": unit_id, "corpus": "A", "sources": [source_id]}
            for unit_id, source_id in zip(candidate_units, page_texts, strict=True)
        ],
    )
    import tiktoken

    encoding = tiktoken.get_encoding("o200k_base")
    rerank_order = [
        {
            "unit_id": unit_id,
            "score": 1 if index == 0 else 1.0 / (index + 1),
        }
        for index, unit_id in enumerate(candidate_units)
    ]
    final_pages = {
        "E": "AS001:001",
        "E_coverage": "AS001:002",
        "F": "AS001:002",
        "F_coverage": "AS001:001",
    }
    rows = []
    for condition, source_id in final_pages.items():
        text = page_texts[source_id]
        rows.append(
            {
                "id": "coverage-01",
                "condition": condition,
                "question_sha256": question_hash,
                "candidate_unit_ids": candidate_units,
                "candidate_source_ids": list(page_texts),
                "reranked_unit_ids": candidate_units,
                "rerank_order": rerank_order,
                "final_sources": [{"source_id": source_id, "text": text}],
                "context_tokens": len(encoding.encode(text)),
                "relation": {"status": "expanded", "event_ids": ["event-1"]},
                "final_selection": {
                    "strategy": "coverage" if "coverage" in condition else "base"
                },
            }
        )
    results = write("coverage-results.jsonl", rows)
    score_dir = tmp_path / "coverage-score"
    score_run(
        results,
        question,
        refs,
        pages,
        score_dir,
        units,
        conditions=("E", "F", "E_coverage", "F_coverage"),
    )

    scores = json.loads((score_dir / "scores.json").read_text())
    by_condition = {row["condition"]: row for row in scores}
    assert by_condition["E_coverage"]["candidate_fact_mask"] == [True]
    assert (
        by_condition["E_coverage"]["candidate_fact_mask"]
        == by_condition["E"]["candidate_fact_mask"]
    )
    assert (
        by_condition["F_coverage"]["candidate_fact_mask"]
        == by_condition["F"]["candidate_fact_mask"]
    )
    assert by_condition["E_coverage"]["relation_exact"]
    assert by_condition["F_coverage"]["relation_exact"]
    assert by_condition["E_coverage"]["relation_wrong_count"] == 0
    assert by_condition["F_coverage"]["relation_wrong_count"] == 0

    pairs = json.loads((score_dir / "changes.json").read_text())
    assert set(pairs) == {"E_F", "E_E_coverage", "F_F_coverage"}
    e_changes = pairs["E_E_coverage"][0]
    assert e_changes["candidate_fact_changes"] == {
        "gained_fact_indices": [],
        "lost_fact_indices": [],
        "gained_fact_count": 0,
        "lost_fact_count": 0,
    }
    assert e_changes["final_fact_changes"] == {
        "gained_fact_indices": [],
        "lost_fact_indices": [1],
        "gained_fact_count": 0,
        "lost_fact_count": 1,
    }
    f_changes = pairs["F_F_coverage"][0]
    assert f_changes["final_fact_changes"] == {
        "gained_fact_indices": [1],
        "lost_fact_indices": [],
        "gained_fact_count": 1,
        "lost_fact_count": 0,
    }
    manifest = json.loads((score_dir / "score-manifest.json").read_text())
    assert manifest["conditions"] == ["E", "F", "E_coverage", "F_coverage"]


def _coverage_fixed_pair_rows(question_hash, candidate_count=20):
    unit_ids = [f"A:page-{index:02d}" for index in range(candidate_count)]
    source_ids = [f"AS001:{index + 1:03d}" for index in range(candidate_count)]
    rerank_order = [
        {"unit_id": unit_id, "score": 1 if index == 0 else 1.0 / (index + 1)}
        for index, unit_id in enumerate(unit_ids)
    ]

    def row(condition):
        return {
            "id": "x",
            "condition": condition,
            "question_sha256": question_hash,
            "candidate_unit_ids": list(unit_ids),
            "candidate_source_ids": list(source_ids),
            "reranked_unit_ids": list(unit_ids),
            "rerank_order": [dict(item) for item in rerank_order],
            "final_sources": [],
            "context_tokens": 0,
            "relation": {"status": "expanded", "event_ids": ["event-1"]},
        }

    return [row("E"), row("E_coverage")]


@pytest.mark.parametrize(
    ("coverage", "baseline"),
    [("E_coverage", "E"), ("F_coverage", "F")],
)
def test_coverage_condition_requires_baseline_before_reading_results(
    tmp_path, coverage, baseline
):
    with pytest.raises(
        ValueError, match=f"{coverage} requires {baseline} in conditions"
    ):
        score_run(
            tmp_path / "missing-results",
            tmp_path / "missing-questions",
            tmp_path / "nonexistent-gold",
            tmp_path / "none",
            tmp_path / "out",
            tmp_path / "units",
            conditions=(coverage,),
        )


@pytest.mark.parametrize(
    "mutation",
    ["candidate_unit_ids", "candidate_source_ids", "rerank_score", "relation"],
)
def test_coverage_fixed_input_mismatch_precedes_reference_read(tmp_path, mutation):
    question_text = "query"
    question_hash = hashlib.sha256(question_text.encode()).hexdigest()
    question = tmp_path / "questions.jsonl"
    question.write_text(json.dumps({"id": "x", "question": question_text}))
    rows = _coverage_fixed_pair_rows(question_hash)
    coverage = rows[1]
    if mutation == "candidate_unit_ids":
        coverage[mutation][-1] = "A:changed:raw"
    elif mutation == "candidate_source_ids":
        coverage[mutation][-1] = "AS999:999"
    elif mutation == "rerank_score":
        coverage["rerank_order"][0]["score"] = 0.25
    else:
        coverage[mutation] = {"status": "expanded", "event_ids": ["different"]}
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError, match="changed fixed input field"):
        score_run(
            results,
            question,
            tmp_path / "nonexistent-gold",
            tmp_path / "none",
            tmp_path / "out",
            tmp_path / "units",
            conditions=("E", "E_coverage"),
        )


@pytest.mark.parametrize(
    "missing_field",
    [
        "candidate_unit_ids",
        "candidate_source_ids",
        "reranked_unit_ids",
        "rerank_order",
        "relation",
    ],
)
def test_coverage_pair_requires_all_fixed_input_fields(tmp_path, missing_field):
    question_text = "query"
    question_hash = hashlib.sha256(question_text.encode()).hexdigest()
    question = tmp_path / "questions.jsonl"
    question.write_text(json.dumps({"id": "x", "question": question_text}))
    rows = _coverage_fixed_pair_rows(question_hash)
    del rows[1][missing_field]
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError, match=f"missing fixed input field {missing_field}"):
        score_run(
            results,
            question,
            tmp_path / "nonexistent-gold",
            tmp_path / "none",
            tmp_path / "out",
            tmp_path / "units",
            conditions=("E", "E_coverage"),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unit_id", "rerank_order unit IDs do not match"),
        ("nan", "scores must be finite"),
        ("infinity", "scores must be finite"),
        ("bool", "scores must be finite"),
    ],
)
def test_coverage_rerank_order_requires_matching_ids_and_finite_scores(
    tmp_path, mutation, message
):
    question_text = "query"
    question_hash = hashlib.sha256(question_text.encode()).hexdigest()
    question = tmp_path / "questions.jsonl"
    question.write_text(json.dumps({"id": "x", "question": question_text}))
    rows = _coverage_fixed_pair_rows(question_hash)
    if mutation == "unit_id":
        rows[1]["rerank_order"][0]["unit_id"] = "A:other:raw"
    elif mutation == "nan":
        rows[1]["rerank_order"][0]["score"] = float("nan")
    elif mutation == "infinity":
        rows[1]["rerank_order"][0]["score"] = float("inf")
    else:
        rows[1]["rerank_order"][0]["score"] = True
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError, match=message):
        score_run(
            results,
            question,
            tmp_path / "nonexistent-gold",
            tmp_path / "none",
            tmp_path / "out",
            tmp_path / "units",
            conditions=("E", "E_coverage"),
        )


@pytest.mark.parametrize(
    ("baseline", "coverage"),
    [("E", "E_coverage"), ("F", "F_coverage")],
)
def test_coverage_pair_requires_exactly_20_candidates_before_reference_read(
    tmp_path, baseline, coverage
):
    question_text = "query"
    question_hash = hashlib.sha256(question_text.encode()).hexdigest()
    question = tmp_path / "questions.jsonl"
    question.write_text(json.dumps({"id": "x", "question": question_text}))
    rows = _coverage_fixed_pair_rows(question_hash, candidate_count=19)
    rows[0]["condition"] = baseline
    rows[1]["condition"] = coverage
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError, match="requires exactly 20 candidate units"):
        score_run(
            results,
            question,
            tmp_path / "nonexistent-gold",
            tmp_path / "none",
            tmp_path / "out",
            tmp_path / "units",
            conditions=(baseline, coverage),
        )
