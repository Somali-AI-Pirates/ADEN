"""Compare source-grounded HG graph expansion with and without clause search."""

import argparse
import json
import re
from collections import Counter
from itertools import zip_longest
from pathlib import Path
from time import perf_counter

from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_clause_binding import IDENTIFIER_FOLDED, roles_in
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_experiment import experiment_config, start_run
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
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    OFFICIAL_SPLITS,
    evaluation_files,
    evaluation_questions,
    hybrid_candidates,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_lookup import (
    EXCLUSION,
    load_target_lookup,
    normalized,
)
from experimental.u_10o0o.context_evidence import _source_key as location
from experimental.u_10o0o.search import load_index

ARCHIVE = Path(__file__).with_name("records") / "advanced-hg-full-fallback-20261109"
BASELINE_ARCHIVE_ARM = "hg_full_fallback"
ARMS = ("baseline", "graph", "clause_graph")
SEED_DEPTH = 10
MAX_SEEDS = 4
MAX_CLAUSES = 4
GRAPH_PAGES = 6
CLAUSE_PAGES = 6
GLOBAL_HEAD = 3
CLAUSE_BREAK = re.compile(
    r"[!?;]\s*|(?<!\d)\.\s+|,\s+|\s+(?:그리고|반면|또한|아울러|한편)\s+"
    r"|\s*(?=(?<!\w)(?:[①②③④]|\([1-4]\)|첫째|둘째|셋째|넷째|"
    r"(?:첫|두|세|네)\s*번째))"
)


def clauses(question, lookup):
    """Use literal clauses, preserving any punctuation inside dates and codes."""
    if EXCLUSION.search(question):
        return ()
    parts = [part.strip(" ,.;?!") for part in CLAUSE_BREAK.split(question)]
    usable = [part for part in parts if len(part.split()) >= 3]
    if 2 <= len(usable) <= MAX_CLAUSES:
        return tuple(usable)
    if len(usable) > MAX_CLAUSES:
        return ()

    text = normalized(question)
    for pattern in (IDENTIFIER_FOLDED, lookup.profile_patterns[0]):
        anchors = list(pattern.finditer(text))
        if 2 <= len(anchors) <= MAX_CLAUSES and len(
            {anchor.group() for anchor in anchors}
        ) == len(anchors):
            starts = [0, *(anchor.start() for anchor in anchors[1:]), len(text)]
            parts = [
                text[left:right].strip() for left, right in zip(starts, starts[1:])
            ]
            if all(len(part.split()) >= 3 for part in parts):
                return tuple(parts)
    return ()


def unique_pages(rows):
    seen = set()
    for row in rows:
        key = location(row[0])
        if key not in seen:
            seen.add(key)
            yield row


def interleave(groups):
    return (row for layer in zip_longest(*groups) for row in layer if row is not None)


def graph_pages(text, ranked, lookup, relations):
    roles = roles_in(text, lookup.rules)
    if not roles or EXCLUSION.search(text):
        return (), []

    codes = _code_matches(text, relations)
    if any(not candidates for candidates in codes.values()):
        return (), []
    profile = _profile(text, lookup)
    filters = [
        set().union(
            *(relations.identities_for(field, value) for value in sorted(values))
        )
        for field, values in profile.items()
    ]
    if dates := _date_candidates(text, None, relations, leading=True):
        # Whole questions can contain independent dates. Clause search can isolate them.
        filters.append(set().union(*dates))
    eligible = set.intersection(*filters) if filters else None
    code_cases = set().union(*codes.values())

    def eligible_case(case):
        return (eligible is None or case in eligible) and (
            not codes or case in code_cases
        )

    from_search = [
        lookup.page_roles[key][0]
        for page, _ in ranked[:SEED_DEPTH]
        if (key := location(page)) in lookup.page_roles
    ]
    repeated = Counter(from_search)
    direct = [
        next(iter(candidates)) for candidates in codes.values() if len(candidates) == 1
    ]
    corroborated = [
        case
        for case in from_search
        if codes or {"shipper", "cargo"}.intersection(profile) or repeated[case] >= 2
    ]
    seeds = tuple(
        dict.fromkeys(case for case in direct + corroborated if eligible_case(case))
    )[:MAX_SEEDS]
    pages = [
        [(page, 0.0) for page in relations.pages_for(case, roles)] for case in seeds
    ]
    return seeds, list(interleave(pages))


def make_pool(base, graph_groups, clause_ranks=()):
    original = set(evaluation.sources(base))

    def additions(groups, limit):
        return [
            row
            for row in unique_pages(interleave(groups))
            if location(row[0]) not in original
        ][:limit]

    graph = additions(graph_groups, GRAPH_PAGES)
    direct = additions([rank[:2] for rank in clause_ranks], CLAUSE_PAGES)
    remainder = interleave((graph, direct, base[GLOBAL_HEAD:]))
    pool = list(unique_pages((*base[:GLOBAL_HEAD], *remainder)))[:CANDIDATES]
    return pool, evaluation.sources(graph), evaluation.sources(direct)


def search_clauses(search, scopes, log):
    queries = list(dict.fromkeys(part for parts in scopes.values() for part in parts))
    if not queries:
        return {}
    log(f"절별 검색 {len(queries)}개 · {len(scopes)}문항")
    sparse = [search.index.fielded(text, top_k=CANDIDATES) for text in queries]
    embeddings = search.embedder.encode_query(
        queries, normalize_embeddings=True, show_progress_bar=False
    )
    _, hybrid = hybrid_candidates(search.pages, search.vectors, sparse, embeddings)
    return dict(zip(queries, hybrid, strict=True))


def prepare_suite(name, config):
    files = evaluation_files("peer" if name == "peer" else "standard")
    if name == "official":
        files = {split: files[split] for split in OFFICIAL_SPLITS}
    archive = ARCHIVE / name
    prior = json.loads((archive / "config.json").read_bytes())
    inputs = {split: evaluation.stamp(q) for split, (q, _) in files.items()}
    refs = {split: evaluation.stamp(r) for split, (_, r) in files.items()}
    current = {**config, "question_inputs": inputs, "references": refs}
    for key in (
        "inputs",
        "limits",
        "model",
        "relation_snapshot",
        "question_inputs",
        "references",
    ):
        if json.loads(json.dumps(current[key])) != prior[key]:
            raise ValueError(f"{name} 저장 기준선과 현재 {key} 지문이 다릅니다.")
    archives = {
        file: evaluation.stamp(archive / file)
        for file in ("rankings.jsonl", "traces.jsonl", "scores.jsonl")
    }
    return files, archive, {**current, "archive": archives}


def run_suite(name, output, search, lookup, storage, relations, config, log):
    files, archive, suite_config = prepare_suite(name, config)
    output.mkdir()
    evaluation.write_json(output / "config.json", suite_config)
    questions = evaluation_questions(files)
    archived = _indexed_rows(archive / "rankings.jsonl", ("id", "arm"))
    trace_rows = _indexed_rows(archive / "traces.jsonl", ("id", "arm"))
    old_scores = _indexed_rows(archive / "scores.jsonl", ("id",))
    page_by_source = {location(page): page for page in search.pages}
    baseline = {
        row["id"]: _restore(archived[row["id"], BASELINE_ARCHIVE_ARM], page_by_source)
        for row in questions
    }
    scopes = {
        row["id"]: parts
        for row in questions
        if baseline[row["id"]][0] and (parts := clauses(row["question"], lookup))
    }
    started = perf_counter()
    clause_ranks = search_clauses(search, scopes, log)
    log(f"{name} 절별 검색 완료 · {perf_counter() - started:.1f}초")
    pools = {arm: [] for arm in ARMS[1:]}
    traces = {}
    changed = Counter()
    for row in questions:
        qid, question = row["id"], row["question"]
        original, _, _, _, route = baseline[qid]
        original_keys = evaluation.sources(original)
        traces[qid, "baseline"] = trace_rows[qid, BASELINE_ARCHIVE_ARM]
        seeds, root_pages = (
            graph_pages(question, original, lookup, relations) if original else ((), [])
        )
        scoped = [
            graph_pages(part, clause_ranks[part], lookup, relations)
            for part in scopes.get(qid, ())
        ]
        for arm in ARMS[1:]:
            use_scopes = arm == "clause_graph" and bool(scoped)
            graph_groups = (
                [pages for _, pages in scoped] + [root_pages]
                if use_scopes
                else [root_pages]
            )
            clause_lists = (
                [clause_ranks[part] for part in scopes[qid]] if use_scopes else []
            )
            pool, graph_sources, clause_sources = make_pool(
                original, graph_groups, clause_lists
            )
            altered = evaluation.sources(pool) != original_keys
            changed[arm] += altered
            pools[arm].append(pool if altered else [])
            traces[qid, arm] = {
                "baseline_route": route,
                "changed_pool": altered,
                "graph_seeds": seeds,
                "clause_seeds": [cases for cases, _ in scoped] if use_scopes else [],
                "clause_count": len(clause_lists),
                "clause_top10": [
                    evaluation.sources(rank[:SEED_DEPTH]) for rank in clause_lists
                ],
                "graph_sources": graph_sources,
                "clause_sources": clause_sources,
            }
    log(f"{name} {len(questions)}문항 · 후보 변경 {dict(changed)}")
    heads = (
        rerank_variants(search.reranker, questions, pools, log)
        if any(changed.values())
        else {}
    )
    retrievals = {}
    for index, row in enumerate(questions):
        qid, question = row["id"], row["question"]
        base = baseline[qid]
        retrievals[qid, "baseline"] = base
        for arm, candidates in pools.items():
            pool = candidates[index]
            trace = traces[qid, arm]
            result = base
            if pool:
                order = retain_reranked_head(pool, heads[arm][index])
                if base[4].startswith("mixed/"):
                    verified, _ = _verified_sources(
                        traces[qid, "baseline"], lookup, relations
                    )
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
                    result = (
                        pool,
                        expanded,
                        context,
                        tuple(verified),
                        f"mixed/full/{route}",
                    )
                else:
                    result, _ = evaluation.finish(
                        question, pool, order, lookup, storage
                    )
                trace["reranked"] = evaluation.sources(order)
            retrievals[qid, arm] = result
            trace["packed"] = evaluation.packed_sources(result[2])
    _, scores, references = evaluation.score(
        output, questions, files, retrievals, traces, ARMS
    )
    groups = {name: scores}
    for row, question in zip(scores, questions, strict=True):
        qid, split = row["id"], question["split"]
        if row["arms"]["baseline"] != old_scores[qid,]["arms"][BASELINE_ARCHIVE_ARM]:
            raise ValueError(f"저장 기준선 점수가 달라졌습니다: {qid}")
        if name == "peer":
            groups.setdefault(split, []).append(row)
            books = {
                cite["book_id"]
                for group in references[split][qid]["evidence_groups"]
                for cite in group
            }
            if split == "peer_multi_doc" and len(books) == 4:
                groups.setdefault("peer_four_doc", []).append(row)
    results = {
        "changed_pools": dict(changed),
        "clause_queries": len(clause_ranks),
        "clause_questions": len(scopes),
        "total_seconds": perf_counter() - started,
        "groups": {
            group: {
                arm: evaluation.summarize([row["arms"][arm] for row in rows])
                for arm in ARMS
            }
            for group, rows in groups.items()
        },
        "paired": {
            arm: {
                "gains": [
                    row["id"]
                    for row in scores
                    if row.get("final_delta", {}).get(arm) == 1
                ],
                "losses": [
                    row["id"]
                    for row in scores
                    if row.get("final_delta", {}).get(arm) == -1
                ],
            }
            for arm in ARMS[1:]
        },
    }
    evaluation.write_json(output / "results.json", results)
    log(
        f"{name} 완료 · {results['total_seconds']:.1f}초 · 저장 기준선 {len(scores)}문항 일치"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--suite", choices=("peer", "official", "both"), default="both")
    args = parser.parse_args()
    output, log = start_run(args.output_dir)
    pages, vectors = load_index()
    config = experiment_config()
    config["relation_snapshot"] = evaluation.stamp(
        HG_RELATIONS_SNAPSHOT / "manifest.json"
    )
    config["graph"] = {
        "seed_depth": SEED_DEPTH,
        "max_seeds_per_query": MAX_SEEDS,
        "max_clauses": MAX_CLAUSES,
        "graph_pages": GRAPH_PAGES,
        "clause_pages": CLAUSE_PAGES,
        "preserved_global_head": GLOBAL_HEAD,
    }
    search = evaluation.Search(pages, vectors)
    lookup, storage = load_target_lookup(pages), load_storage_lookup(pages)
    with HGRelations(pages=pages) as relations:
        suites = ("official", "peer") if args.suite == "both" else (args.suite,)
        for name in suites:
            run_suite(
                name, output / name, search, lookup, storage, relations, config, log
            )


if __name__ == "__main__":
    main()
