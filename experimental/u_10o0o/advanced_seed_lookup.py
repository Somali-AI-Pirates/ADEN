"""Measure candidate expansion from a verified case key on the first search hit."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import fmean
from time import perf_counter

from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_references
from app.ingestion.curation import read_rows, write_rows
from app.ingestion.layout import CASE_ID, identifiers
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import ProfileIndex, rank_with_hg_lookup
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_rank_policy import BASE, load_reference_suites, pack
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    FINAL_K,
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    OFFICIAL_SPLITS,
    RERANKED_HEAD,
    rerank_variants,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_benchmark import (
    USAGE,
    fingerprint,
    ranked_sources,
    save_rankings,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.advanced_target_metrics import (
    _audit_quotes,
    _context_pages,
    _evidence_group_metrics,
)
from experimental.u_10o0o.advanced_vocabulary import regression_questions
from experimental.u_10o0o.advanced_workflow_eval import local_output
from experimental.u_10o0o.prepare import CACHE_PATH

BUDGET = 8000
HEADER_LINES = 5
MAX_CASE_PAGES = 5
EXPANDED = "seed_lookup"


def source_key(source):
    return source["book_id"], source["page"]


def case_index(source_pages, restored):
    links = defaultdict(set)
    seen = set()
    for source in read_rows(source_pages):
        page = source_key(source)
        if page in seen or page not in restored:
            raise ValueError("원본 머리말의 페이지 위치가 복원 페이지와 다릅니다.")
        seen.add(page)
        lines = [line.strip() for line in source["text"].splitlines() if line.strip()]
        for key in identifiers("\n".join(lines[:HEADER_LINES])):
            if CASE_ID.match(key):
                links[key].add(page)
    if seen != restored.keys():
        raise ValueError("원본 머리말 색인과 복원 페이지 범위가 다릅니다.")
    verified = {}
    by_page = defaultdict(set)
    for key, pages in links.items():
        if (
            2 <= len(pages) <= MAX_CASE_PAGES
            and len({book for book, _ in pages}) == len(pages)
            and all(key in identifiers(restored[page]["text"]) for page in pages)
        ):
            verified[key] = tuple(sorted(pages))
            for page in pages:
                by_page[page].add(key)
    return verified, by_page


def candidate_additions(question, baseline, cases, by_page):
    if identifiers(question) or baseline["route"] in {"explicit_hg", "indirect_hg"}:
        return "existing_lookup_or_identifier", None, ()
    if not baseline["expanded"]:
        return "empty_ranking", None, ()
    keys = by_page.get(source_key(baseline["expanded"][0]), ())
    if len(keys) != 1:
        return "ambiguous_seed" if keys else "unlinked_seed", None, ()
    key = next(iter(keys))
    present = {source_key(source) for source in baseline["pool"]}
    additions = tuple(page for page in cases[key] if page not in present)
    return "expanded" if additions else "already_in_pool", key, additions


def archived_baselines(path, ids):
    rows = [
        r
        for r in read_rows(path / "rankings.jsonl")
        if r["arm"] == BASE and r["id"] in ids
    ]
    if len(rows) != len(ids) or {r["id"] for r in rows} != ids:
        raise ValueError("보관된 기준선의 질문 범위가 다릅니다.")
    return {r["id"]: r for r in rows}


def benchmark(questions, baselines, restored, cases, by_page, log):
    retrievals, active, decisions = {}, [], {}
    pools = {BASE: [], EXPANDED: []}
    for q in questions:
        qid = q["id"]
        saved = baselines[qid]
        pool, expanded = (
            [
                (restored[source_key(x)], -float(rank))
                for rank, x in enumerate(saved[stage], start=1)
            ]
            for stage in ("pool", "expanded")
        )
        context = pack(expanded, BUDGET)
        if (
            len(pool) != CANDIDATES
            or len({location(p) for p, _ in pool}) != CANDIDATES
            or context.context_hash != saved["context_sha256"]
        ):
            raise ValueError(f"{qid}의 보관 기준선과 문맥이 다릅니다.")
        retrievals[qid, BASE] = pool, expanded, context, (), saved["route"]
        decision, key, additions = candidate_additions(
            q["question"], saved, cases, by_page
        )
        decisions[qid] = decision, key, additions
        if not additions:
            retrievals[qid, EXPANDED] = retrievals[qid, BASE]
            continue
        active.append(q)
        pools[BASE].append(pool)
        pools[EXPANDED].append(
            pool[: -len(additions)]
            + [
                (restored[page], -float(CANDIDATES - len(additions) + i))
                for i, page in enumerate(additions, start=1)
            ]
        )
    log(f"기준선 문맥 지문 {len(questions)}개 일치 · 후보 보충 {len(active)}문항")
    if not active:
        return retrievals, decisions

    import torch

    model = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    rankings = rerank_variants(model, active, pools, log)
    lookup = load_target_lookup(list(restored.values()))
    profiles = ProfileIndex(lookup.profiles)
    for i, q in enumerate(active):
        qid, query = q["id"], q["question"]
        for arm in (BASE, EXPANDED):
            pool = pools[arm][i]
            order = retain_reranked_head(pool, rankings[arm][i])
            linked, route = rank_with_hg_lookup(query, order, lookup.cases, profiles)
            linked = linked[:CANDIDATES]
            if arm == BASE:
                old = baselines[qid]
                if ranked_sources(linked) != old["expanded"] or route != old["route"]:
                    raise ValueError(f"{qid}의 기준선 KURE 순위를 재현하지 못했습니다.")
            else:
                retrievals[qid, arm] = pool, linked, pack(linked, BUDGET), (), route
    return retrievals, decisions


def score(questions, retrievals, decisions, refs, cases, audit):
    rows = []
    for q in questions:
        qid, group = q["id"], q["cohort"]
        ref = refs[qid]
        groups = [{source_key(x) for x in g} for g in ref["evidence_groups"] if g]
        gold = set().union(*groups)
        books = {book for book, _ in gold}
        decision, key, additions = decisions[qid]
        possible = set(cases.get(key, ()))
        strict, reanchored = _audit_quotes(audit, qid)
        arms = {}
        for arm in (BASE, EXPANDED):
            pool, _, context, _, _ = retrievals[qid, arm]
            candidate = {location(p) for p, _ in pool}
            final = {(c.chunk.book_id, c.chunk.page) for c in context.final_chunks}
            if (
                len(candidate) != CANDIDATES
                or len(final) > FINAL_K
                or context.token_count > BUDGET
            ):
                raise ValueError(f"{qid}의 후보 수 또는 문맥 한도가 다릅니다.")
            found_books = len(books & {book for book, _ in final})
            if group == "identifier":
                complete = bool(fact_coverage(ref, context)["all_facts"])
            else:
                matches = _evidence_group_metrics(
                    ref,
                    _context_pages(context),
                    strict_quotes=strict if group == "synth4" else None,
                    reanchored_quotes=reanchored,
                )
                complete = matches["all_scoreable_evidence_groups"]
            arms[arm] = {
                "candidate_pages": len(gold & candidate),
                "final_pages": len(gold & final),
                "all_candidate_pages": bool(gold) and gold <= candidate,
                "document_recall": found_books / len(books) if books else None,
                "all_documents": bool(books) and found_books == len(books),
                "complete": complete,
                "context_tokens": context.token_count,
                "budget_excluded": len(context.excluded_chunk_ids),
            }
        rows.append(
            {
                "id": qid,
                "cohort": group,
                "multi_page": bool(groups) and not set.intersection(*groups),
                "required_pages": len(gold),
                "decision": decision,
                "added_pages": [{"book_id": b, "page": p} for b, p in additions],
                "added_gold_pages": len(set(additions) & gold),
                "seed_group_overlaps_reference": bool(possible & gold)
                if possible and gold
                else None,
                "arms": arms,
            }
        )
    return rows


def summarize(rows):
    output = {}
    for group in ("official", "official_multi_page", "synth4", "identifier"):
        selected = [
            r
            for r in rows
            if r["cohort"] == group
            or (
                group == "official_multi_page"
                and r["cohort"] == "official"
                and r["multi_page"]
            )
        ]
        scored = [r for r in selected if r["required_pages"]]
        changed = [r for r in selected if r["decision"] == "expanded"]
        output[group] = {
            "questions": len(selected),
            "scored": len(scored),
            "required_pages": sum(r["required_pages"] for r in scored),
            "decisions": dict(Counter(r["decision"] for r in selected)),
            "changed_reference_disjoint": sum(
                r["seed_group_overlaps_reference"] is False for r in changed
            ),
            "changed_reference_overlap": sum(
                r["seed_group_overlaps_reference"] is True for r in changed
            ),
            "added_gold_pages": sum(r["added_gold_pages"] for r in changed),
            "arms": {
                arm: {
                    **{
                        key: sum(r["arms"][arm][key] or 0 for r in scored)
                        for key in (
                            "candidate_pages",
                            "final_pages",
                            "all_candidate_pages",
                            "all_documents",
                            "complete",
                            "budget_excluded",
                        )
                    },
                    "candidate_page_recall": fmean(
                        r["arms"][arm]["candidate_pages"] / r["required_pages"]
                        for r in scored
                    ),
                    "page_recall": fmean(
                        r["arms"][arm]["final_pages"] / r["required_pages"]
                        for r in scored
                    ),
                    "document_recall": fmean(
                        r["arms"][arm]["document_recall"] for r in scored
                    ),
                }
                for arm in (BASE, EXPANDED)
            },
            "complete_gain": sum(
                bool(r["arms"][EXPANDED]["complete"])
                and not r["arms"][BASE]["complete"]
                for r in scored
            ),
            "complete_loss": sum(
                bool(r["arms"][BASE]["complete"])
                and not r["arms"][EXPANDED]["complete"]
                for r in scored
            ),
        }
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reference-run", "vocabulary-run", "quote-audit", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    reference, vocabulary, audit, output = map(
        local_output,
        (args.reference_run, args.vocabulary_run, args.quote_audit, args.output),
    )
    source_config = json.loads((reference / "results.json").read_bytes())
    vocabulary_config = json.loads((vocabulary / "results.json").read_bytes())
    files, official_and_peer, inputs = load_reference_suites(audit)
    regression, stamps = regression_questions()
    if (
        source_config["inputs"] != inputs
        or source_config["pages"] != fingerprint(CACHE_PATH)
        or source_config["quote_audit"] != fingerprint(audit)
        or vocabulary_config["regression_inputs"] != stamps
        or any(
            cfg["limits"]["tokens"] != BUDGET
            or cfg["limits"]["pages"] != FINAL_K
            or cfg["limits"]["candidates"] != CANDIDATES
            or cfg["limits"]["head"] != RERANKED_HEAD
            for cfg in (source_config, vocabulary_config)
        )
    ):
        raise ValueError("기존 벤치의 데이터 지문 또는 문맥 조건이 다릅니다.")
    official = [
        {**q, "cohort": "official"}
        for q in official_and_peer
        if q["split"] in OFFICIAL_SPLITS
    ]
    synth4 = [
        {**q, "cohort": "synth4"}
        for q in official_and_peer
        if q["split"] == "peer_multi_doc" and q["id"].startswith(("SY4-", "SYF4-"))
    ]
    questions = official + synth4 + [{**q, "cohort": "identifier"} for q in regression]
    if (
        len(official),
        len(synth4),
        len(regression),
        len({q["id"] for q in questions}),
    ) != (60, 78, 160, 298):
        raise ValueError("고정 공식·합성·번호 평가 범위가 다릅니다.")
    baselines = archived_baselines(
        reference, {q["id"] for q in official + synth4}
    ) | archived_baselines(vocabulary, {q["id"] for q in regression})
    output.mkdir(parents=True, exist_ok=False)
    started = perf_counter()

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        print(line, flush=True)

    restored = {location(page): page for page in json.loads(CACHE_PATH.read_bytes())}
    source_pages = reference / "peer-index/adv/pages.jsonl"
    cases, by_page = case_index(source_pages, restored)
    log(f"원본 머리말 사건 키 {len(cases):,}개 확인 · {len(questions)}문항")
    retrievals, decisions = benchmark(
        questions, baselines, restored, cases, by_page, log
    )
    save_rankings(output, retrievals)
    log("검색 순위와 문맥 저장 완료 · 참고 근거 채점 시작")
    refs = {
        qid: ref
        for split in (*OFFICIAL_SPLITS, "peer_multi_doc")
        for qid, ref in load_references(files[split][1]).items()
    }
    refs.update(load_references(USAGE / "blind_references.json"))
    if {q["id"] for q in synth4} != {
        qid
        for qid in load_references(files["peer_multi_doc"][1])
        if len({e["book_id"] for g in refs[qid]["evidence_groups"] for e in g}) == 4
    }:
        raise ValueError("4문서 합성 78문항의 참고 근거 범위가 다릅니다.")
    rows = score(
        questions, retrievals, decisions, refs, cases, json.loads(audit.read_bytes())
    )
    write_rows(output / "scores.jsonl", rows)
    write_json(
        output / "results.json",
        {
            "code": fingerprint(Path(__file__)),
            "reference_rankings": fingerprint(reference / "rankings.jsonl"),
            "vocabulary_rankings": fingerprint(vocabulary / "rankings.jsonl"),
            "source_pages": fingerprint(source_pages),
            "restored_pages": fingerprint(CACHE_PATH),
            "quote_audit": fingerprint(audit),
            "inputs": inputs,
            "regression": stamps,
            "limits": {
                "candidates": CANDIDATES,
                "pages": FINAL_K,
                "tokens": BUDGET,
                "reranked_head": RERANKED_HEAD,
            },
            "seed": {
                "rank": 1,
                "header_lines": HEADER_LINES,
                "min_case_pages": 2,
                "max_case_pages": MAX_CASE_PAGES,
                "verified_keys": len(cases),
            },
            "external_api_calls": 0,
            "reranker": {
                "name": KURE,
                "revision": MODELS[KURE],
                "max_length": MAX_TOKENS,
                "dtype": "float16",
                "device": "cuda",
            },
            "summary": summarize(rows),
        },
    )
    log(f"채점 완료 · 외부 API 0회 · {perf_counter() - started:.1f}초")


if __name__ == "__main__":
    main()
