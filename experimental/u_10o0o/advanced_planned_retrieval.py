"""Ablate question planning, typed lookups, dependencies and evidence selection."""

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from app.evaluation.dataset import load_references
from app.generation.context import build_context
from app.ingestion.curation import write_rows
from app.ingestion.layout import identifiers
from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_clause_binding import roles_in
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_experiment import artifact_path, experiment_config
from experimental.u_10o0o.advanced_graph_benchmark import (
    ARCHIVE,
    BASELINE_ARCHIVE_ARM,
    clauses,
    interleave,
    unique_pages,
)
from experimental.u_10o0o.advanced_hg_fallback_replay import (
    _indexed_rows,
    _restore,
    _verified_sources,
)
from experimental.u_10o0o.advanced_hg_partition import attach_verified_sources
from experimental.u_10o0o.advanced_hg_relations import (
    HG_RELATIONS_SNAPSHOT,
    HGRelations,
)
from experimental.u_10o0o.advanced_hg_route import (
    _code_matches,
    _date_candidates,
    _profile,
)
from experimental.u_10o0o.advanced_query_plan import (
    DEPENDENCY_FIELDS,
    ROLE_NAMES,
    Plan,
    task,
)
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    DEFAULT_CONTEXT_TOKENS,
    FINAL_K,
    OFFICIAL_SPLITS,
    evaluation_files,
    hybrid_candidates,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_structured_store import StructuredStore
from experimental.u_10o0o.advanced_target_lookup import (
    DATE,
    EXCLUSION,
    load_target_lookup,
)
from experimental.u_10o0o.context_evidence import (
    _context_candidates,
    _count_tokens,
    _source_key,
)
from experimental.u_10o0o.search import load_index

MONTH = re.compile(r"(?<!\d)(20\d{2})-(0[1-9]|1[0-2])(?![-\d])")
ARMS = ("baseline", "rules", "parallel", "typed", "dependent", "coverage", "authored")
TYPED_ARMS = frozenset(("rules", "typed", "dependent", "coverage", "authored"))
DEPENDENT_ARMS = frozenset(("rules", "dependent", "coverage", "authored"))
COVERAGE_ARMS = frozenset(("rules", "coverage", "authored"))
SHARED_BASE_HEAD = 3
PER_TASK = 4
TASK_RERANK_DEPTH = 8


@dataclass
class Result:
    query: str
    cases: tuple[str, ...] = ()
    pages: tuple[dict, ...] = ()
    dependency_used: bool = False


def rule_plan(question, lookup):
    roles = {value: key for key, value in ROLE_NAMES.items()}
    steps = [
        task(
            "case"
            if identifiers(part.upper())
            or lookup._matched_profile_values(part).get("shipper")
            else "text",
            part,
            [roles[role] for role in roles_in(part)],
        )
        for part in (clauses(question, lookup) or (question,))
    ]
    return Plan(tasks=steps)


def reported_values(store, case, field):
    return {
        row.value
        for row in store.get_hg_facts(case, fact_code=field)
        if row.state == "reported" and isinstance(row.value, str)
    }


def linked_value(step, previous, store):
    if not (0 < step.after <= len(previous)) or step.use not in DEPENDENCY_FIELDS:
        return None
    prerequisite = previous[step.after - 1]
    if len(prerequisite.cases) != 1:
        return None
    sources = set(map(_source_key, prerequisite.pages))
    values = {
        row.value
        for row in store.get_hg_facts(
            prerequisite.cases[0], fact_code=DEPENDENCY_FIELDS[step.use]
        )
        if row.state == "reported"
        and isinstance(row.value, str)
        and (row.source.book_id, row.source.page) in sources
    }
    return next(iter(values)) if len(values) == 1 else None


def case_scope(anchor, step, lookup, relations, store, contract=None):
    codes = _code_matches(anchor, relations)
    if any(not matches for matches in codes.values()):
        return ()
    filters = list(codes.values())
    if contract is not None:
        filters.append(relations.code_candidates(contract, "contract_id"))
    profile = _profile(anchor, lookup)
    if any(len(values) != 1 for values in profile.values()):
        return ()
    filters.extend(
        relations.identities_for(key, next(iter(values)))
        for key, values in profile.items()
    )
    dates = _date_candidates(anchor, None, relations, leading=False)
    if dates is None:
        return ()
    filters.extend(dates)
    if not filters:
        return ()
    cases = set.intersection(*filters)
    if step.kind == "case":
        strong = (
            codes
            or contract
            or ({"shipper", "cargo"}.intersection(profile) and len(filters) >= 2)
        )
        return tuple(cases) if strong and len(cases) == 1 else ()
    months = MONTH.findall(anchor)
    if (
        step.condition != "late_return"
        or not {"shipper", "spec"} <= profile.keys()
        or len(months) != 1
    ):
        return ()
    month = "-".join(months[0])
    delayed = []
    for case in sorted(cases):
        actual = reported_values(store, case, "actual_designated_return_date")
        expected = reported_values(store, case, "scheduled_return_date")
        if len(actual) == len(expected) == 1:
            a, b = next(iter(actual)), next(iter(expected))
            if a.startswith(month + "-") and a > b:
                delayed.append(case)
    return tuple(delayed) if 1 <= len(delayed) <= 4 else ()


def resolve(question, plan, arm, lookup, relations, store):
    steps = []
    question_codes = set(identifiers(question.upper()))
    question_dates = set(DATE.findall(question))
    for step in plan.tasks:
        if step.anchor not in question:
            steps.append(Result(question))
            continue
        # Keep the model's rewrite in the search path only. DB conditions are grounded
        # in the literal anchor, and search rewrites may not invent exact keys or dates.
        rewrite = step.query
        if (
            set(identifiers(rewrite.upper())) - question_codes
            or set(DATE.findall(rewrite)) - question_dates
            or set(MONTH.findall(rewrite)) - set(MONTH.findall(question))
            or (EXCLUSION.search(question) and not EXCLUSION.search(rewrite))
        ):
            rewrite = question
        dependency = linked_value(step, steps, store) if arm in DEPENDENT_ARMS else None
        if dependency is not None:
            rewrite = f"{dependency} {rewrite}"
        cases = ()
        if (
            arm in TYPED_ARMS
            and step.kind in {"case", "case_set"}
            and step.roles
            and not EXCLUSION.search(question)
            and (not step.after or dependency is not None)
        ):
            cases = case_scope(
                step.anchor,
                step,
                lookup,
                relations,
                store,
                dependency if step.use == "contract" else None,
            )
        wanted = tuple(dict.fromkeys(ROLE_NAMES[role] for role in step.roles))
        pages = tuple(interleave([relations.pages_for(case, wanted) for case in cases]))
        steps.append(Result(rewrite, cases, pages, dependency is not None))
    return steps


def search_queries(search, queries):
    queries = list(dict.fromkeys(queries))
    if not queries:
        return {}
    sparse = [search.index.fielded(text, top_k=CANDIDATES) for text in queries]
    embeddings = search.embedder.encode_query(
        queries, normalize_embeddings=True, show_progress_bar=False
    )
    _, ranked = hybrid_candidates(search.pages, search.vectors, sparse, embeddings)
    return dict(zip(queries, ranked, strict=True))


def rerank_tasks(search, ranks, queries, log):
    queries = list(dict.fromkeys(queries))
    if not queries:
        return {}
    heads = [ranks[query][:TASK_RERANK_DEPTH] for query in queries]
    ranked = rerank_variants(
        search.reranker,
        [{"question": query} for query in queries],
        {"task": heads},
        log,
    )["task"]
    return {
        query: head + ranks[query][TASK_RERANK_DEPTH:]
        for query, head in zip(queries, ranked, strict=True)
    }


def merge_pool(base, results, ranks):
    groups = [
        list(unique_pages([(page, 0.0) for page in step.pages] + ranks[step.query]))[
            :PER_TASK
        ]
        for step in results
    ]
    nominated = list(unique_pages(interleave(groups)))
    return list(
        unique_pages([*base[:SHARED_BASE_HEAD], *nominated, *base[SHARED_BASE_HEAD:]])
    )[:CANDIDATES]


def keep_coverage(result, steps, ranks):
    pool, expanded, context, cases, route = result
    if not steps:
        return result
    available = {_source_key(page) for page, _ in expanded}
    groups = []
    for step in steps:
        verified = [
            (page, 0.0) for page in step.pages if _source_key(page) in available
        ]
        fallback = [
            row for row in ranks[step.query] if _source_key(row[0]) in available
        ][:1]
        groups.append(verified or fallback)
    if len(groups) < 2 and sum(len(group) for group in groups) < 2:
        return result
    current = set(evaluation.packed_sources(context))
    preserved = [row for row in expanded if _source_key(row[0]) in current]
    selected = list(unique_pages([*interleave(groups), *preserved, *expanded]))
    revised = build_context(
        _context_candidates(selected), FINAL_K, DEFAULT_CONTEXT_TOKENS, _count_tokens
    )
    return pool, expanded, revised, cases, route


def make_archive(questions, page_map):
    archived, traces, scores = {}, {}, {}
    for suite in ("official", "peer"):
        records = ARCHIVE / suite
        ranks = _indexed_rows(records / "rankings.jsonl", ("id", "arm"))
        logs = _indexed_rows(records / "traces.jsonl", ("id", "arm"))
        points = _indexed_rows(records / "scores.jsonl", ("id",))
        for row in questions:
            split = row["split"]
            if (suite == "official" and split in OFFICIAL_SPLITS) or (
                suite == "peer" and split.startswith("peer_")
            ):
                key = row["id"], BASELINE_ARCHIVE_ARM
                archived[row["id"]] = _restore(ranks[key], page_map)
                traces[row["id"]] = logs[key]
                scores[row["id"]] = points[row["id"],]["arms"][BASELINE_ARCHIVE_ARM]
    return archived, traces, scores


def plan_shape(plan):
    # For authored controls compare request kind, role, condition and dependence
    # separately from exact event resolution and downstream page scoring.
    if plan is None:
        return Counter()
    return Counter(
        (s.kind, tuple(sorted(s.roles)), s.condition, s.after, s.use)
        for s in plan.tasks
    )


def run(source, output, log):
    questions = json.loads((source / "questions.json").read_bytes())
    submitted = _indexed_rows(source / "luna_plans.jsonl", ("id",))
    if {row["id"] for row in questions} != {qid for (qid,) in submitted}:
        raise ValueError("평가 질문과 Luna 계획 기록 범위가 일치하지 않습니다.")
    plans = {
        qid: Plan.model_validate(row["plan"]) if row["plan"] else None
        for (qid,), row in submitted.items()
    }
    authored = {
        qid: Plan.model_validate(plan)
        for qid, plan in json.loads(
            (source / "authored_plans.json").read_bytes()
        ).items()
    }
    pages, vectors = load_index()
    page_map = {_source_key(page): page for page in pages}
    archived, traces, old_scores = make_archive(questions, page_map)
    lookup, storage = load_target_lookup(pages), load_storage_lookup(pages)
    search = evaluation.Search(pages, vectors)
    scopes = {}
    baseline = dict(archived)
    replacements = {}
    baselines_to_search = [q["question"] for q in questions if q["id"] not in baseline]
    with HGRelations(pages=pages) as relations, StructuredStore() as store:
        for row in questions:
            qid, question = row["id"], row["question"]
            rules = rule_plan(question, lookup)
            for arm in ARMS[1:]:
                plan = (
                    rules
                    if arm == "rules"
                    else authored.get(qid)
                    if arm == "authored"
                    else plans[qid]
                )
                if plan is None or (qid in archived and not archived[qid][0]):
                    continue
                if arm == "coverage":
                    scopes[qid, arm] = scopes.get((qid, "dependent"), [])
                else:
                    scopes[qid, arm] = resolve(
                        question, plan, arm, lookup, relations, store
                    )
        task_queries = [s.query for steps in scopes.values() for s in steps]
        raw_queries = baselines_to_search + task_queries
        log(f"로컬 5:5 검색 · 중복 제거 {len(set(raw_queries))}개")
        initial_ranks = search_queries(search, raw_queries)
        ranks = rerank_tasks(search, initial_ranks, task_queries, log)
        candidate_sets = {arm: [] for arm in ARMS if arm != "coverage"}
        for q in questions:
            qid, question = q["id"], q["question"]
            initial = baseline[qid][0] if qid in baseline else initial_ranks[question]
            candidate_sets["baseline"].append(initial if qid not in baseline else [])
            for arm in candidate_sets.keys() - {"baseline"}:
                steps = scopes.get((qid, arm), [])
                pool = merge_pool(initial, steps, ranks) if steps else initial
                changed = bool(steps) and evaluation.sources(
                    initial
                ) != evaluation.sources(pool)
                candidate_sets[arm].append(pool if changed else [])
                if changed:
                    replacements[qid, arm] = pool
        heads = rerank_variants(search.reranker, questions, candidate_sets, log)
        results = {}
        for i, row in enumerate(questions):
            qid, question = row["id"], row["question"]
            if qid not in baseline:
                initial = initial_ranks[question]
                order = retain_reranked_head(initial, heads["baseline"][i])
                baseline[qid], _ = evaluation.finish(
                    question, initial, order, lookup, storage
                )
            results[qid, "baseline"] = baseline[qid]
            for arm in ARMS[1:]:
                if arm == "authored" and qid not in authored:
                    continue
                if arm == "coverage":
                    result = results[qid, "dependent"]
                elif pool := replacements.get((qid, arm)):
                    order = retain_reranked_head(pool, heads[arm][i])
                    if baseline[qid][4].startswith("mixed/"):
                        verified, _ = _verified_sources(traces[qid], lookup, relations)
                        expanded, context, route = attach_verified_sources(
                            question,
                            pool,
                            order,
                            lookup,
                            verified,
                            requested_roles={case: ROLES for case in verified},
                            mixed=True,
                            allow_deferred_search_pages=True,
                        )
                        result = pool, expanded, context, tuple(verified), route
                    else:
                        result, _ = evaluation.finish(
                            question, pool, order, lookup, storage
                        )
                else:
                    result = baseline[qid]
                if arm in COVERAGE_ARMS:
                    result = keep_coverage(result, scopes.get((qid, arm), []), ranks)
                results[qid, arm] = result

    # Persist candidate and context IDs before loading labels for scoring.
    evaluation.save_rankings(output, results)
    write_rows(
        output / "task_traces.jsonl",
        (
            {
                "id": qid,
                "arm": arm,
                "tasks": len(steps),
                "verified_cases": [step.cases for step in steps],
                "db_sources": [list(map(_source_key, step.pages)) for step in steps],
                "dependency_uses": sum(step.dependency_used for step in steps),
            }
            for (qid, arm), steps in scopes.items()
        ),
    )
    files = evaluation_files("all")
    references = {
        split: load_references(files[split][1])
        for split in {q["split"] for q in questions if q["id"] in archived}
    }
    control = json.loads((source / "control_references.json").read_bytes())
    score_rows = []
    cohorts = defaultdict(list)
    for row in questions:
        qid, split = row["id"], row["split"]
        ref = control[qid] if qid in control else references[split][qid]
        scores = {
            arm: evaluation.score_one(
                ref, results[qid, arm], peer=split.startswith("peer_")
            )
            for arm in ARMS
            if (qid, arm) in results
        }
        if qid in old_scores and scores["baseline"] != old_scores[qid]:
            raise ValueError(f"기준선 재현 불일치: {qid}")
        point = {"id": qid, "split": split, "arms": scores}
        if qid in authored:
            point["luna_plan_matches_authored"] = plan_shape(plans[qid]) == plan_shape(
                authored[qid]
            )
            point["rules_plan_matches_authored"] = plan_shape(
                rule_plan(row["question"], lookup)
            ) == plan_shape(authored[qid])
        score_rows.append(point)
        cohort = (
            "controls"
            if split.startswith("control_")
            else "peer"
            if split.startswith("peer_")
            else "official"
        )
        cohorts[cohort].append(point)
        if cohort != "official":
            cohorts[split].append(point)
    write_rows(output / "scores.jsonl", score_rows)
    totals = {
        group: {
            arm: evaluation.summarize(
                [p["arms"][arm] for p in rows if arm in p["arms"]]
            )
            for arm in ARMS
            if any(arm in p["arms"] for p in rows)
        }
        for group, rows in cohorts.items()
    }
    usage_rows = [r for r in submitted.values() if r["usage"]]
    evaluation.write_json(
        output / "results.json",
        {
            "metrics": totals,
            "calls": dict(Counter(r["status"] for r in submitted.values())),
            "usage": {
                key: sum(r["usage"][key] for r in usage_rows)
                for key in ("input_tokens", "output_tokens", "total_tokens")
            },
            "cached_input_tokens": sum(
                r["usage"]["input_tokens_details"]["cached_tokens"] for r in usage_rows
            ),
            "plan_latency": evaluation.latency(
                [r["seconds"] * 1000 for r in submitted.values()]
            ),
            "controls_plan_shape": {
                name: sum(
                    p[f"{name}_plan_matches_authored"] for p in cohorts["controls"]
                )
                for name in ("rules", "luna")
            },
            "dependency_uses": {
                arm: sum(
                    s.dependency_used
                    for (qid, a), steps in scopes.items()
                    if a == arm
                    for s in steps
                )
                for arm in ARMS
            },
        },
    )
    log(f"검색·근거 채점 완료 · {len(score_rows)}문항 · {len(results)}개 결과")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    source = args.run_directory.resolve()
    output = artifact_path(args.output_dir or source / "retrieval")
    output.mkdir()
    started = perf_counter()

    def log(message):
        line = f"{perf_counter() - started:.1f}s {message}"
        print(line, flush=True)
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    config = experiment_config()
    config["relation_snapshot"] = evaluation.stamp(
        HG_RELATIONS_SNAPSHOT / "manifest.json"
    )
    config["planner"] = {
        name: evaluation.stamp(source / name)
        for name in (
            "questions.json",
            "planner_config.json",
            "luna_plans.jsonl",
            "authored_plans.json",
        )
    }
    config["max_pages_per_task"] = PER_TASK
    config["task_rerank_depth"] = TASK_RERANK_DEPTH
    config["preserved_global_head"] = SHARED_BASE_HEAD
    evaluation.write_json(output / "config.json", config)
    run(source, output, log)


if __name__ == "__main__":
    main()
