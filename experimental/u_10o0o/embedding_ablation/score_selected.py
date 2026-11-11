"""Score explicitly selected completed arms without requiring deferred models."""

import argparse
from pathlib import Path

from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_retrieve import loaded_runtime

from .__main__ import DEFAULT_RUN, validate_inputs, validate_results
from .assets import MODELS


def score(run, names, output):
    if names[0] != "snowflake" or len(set(names)) != len(names) or len(names) < 2:
        raise ValueError("Select Snowflake first and at least one distinct replacement")
    if not set(names) <= set(MODELS):
        raise ValueError("Unknown model")
    frozen = validate_inputs(run)
    saved = {name: validate_results(run, name, frozen) for name in names}
    score_saved(frozen, saved, output)


def score_saved(frozen, saved, output):
    """Score already validated traces and contexts with the common criteria."""
    names = tuple(saved)
    # The full selected comparison is saved and validated before any gold read.
    from app.evaluation.dataset import load_references
    from experimental.u_10o0o.advanced_full_metrics import fact_coverage
    from experimental.u_10o0o.pattern_retrieval.established_score import (
        aggregates,
        category,
        differences,
        official_metrics,
        page_key,
        page_metrics,
        score_item,
    )
    from experimental.u_10o0o.prepare import CACHE_PATH

    references = {
        s["suite"]: load_references(ROOT / s["references"])
        for s in frozen["question_sources"]
    }
    source_pages = {page_key(row): row for row in read_json(CACHE_PATH)}
    by_arm, dense = {}, {}
    for name, (traces, contexts) in saved.items():
        rows, dense_rows = [], []
        for meta, trace, context in zip(frozen["items"], traces, contexts, strict=True):
            reference = references[meta["suite"]][meta["question_id"]]
            rows.append(score_item(meta, reference, trace, context, source_pages))
            for k in (20, 50):
                pages = [source_pages[page_key(p)] for p in trace["dense_top50"][:k]]
                if meta["suite"].startswith("official_"):
                    metrics = official_metrics(reference, pages, cutoff=k)
                    complete = metrics["all_groups"] if metrics else None
                elif meta["suite"] != "negative_control":
                    expected = {
                        page_key(e)
                        for requirement in reference["evidence_requirements"]
                        for e in requirement["primary"]
                    }
                    metrics = {
                        **page_metrics(expected, pages, cutoff=k),
                        **fact_coverage(reference, pages),
                    }
                    complete = metrics["all_facts"]
                else:
                    metrics, complete = None, None
                dense_rows.append(
                    {
                        **meta,
                        "category": category(reference, meta["suite"]),
                        "k": k,
                        "complete": complete,
                        "metrics": metrics,
                    }
                )
        by_arm[name], dense[name] = rows, dense_rows
    output.mkdir(exist_ok=False)
    write_json(output / "per-question.json", by_arm)
    write_json(output / "dense-per-question.json", dense)
    write_json(
        output / "summary.json",
        {name: aggregates(rows) for name, rows in by_arm.items()},
    )
    write_json(
        output / "differences.json",
        {
            name: differences({"baseline": by_arm["snowflake"], "A": by_arm[name]})
            for name in names[1:]
        },
    )
    write_json(
        output / "manifest.json",
        {
            "selected_models": names,
            "deferred_models": [name for name in MODELS if name not in names],
            "all_selected_results_saved_before_references": True,
            "references": [
                {"path": s["references"], "sha256": file_sha(ROOT / s["references"])}
                for s in frozen["question_sources"]
            ],
            "runtime_files": loaded_runtime(),
        },
    )
    print("Scored selected models:", ", ".join(names), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--models", nargs="+", choices=tuple(MODELS), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    score(args.run, args.models, args.output or args.run / "scores-selected")
