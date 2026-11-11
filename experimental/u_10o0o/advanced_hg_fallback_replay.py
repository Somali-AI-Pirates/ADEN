"""Replay the same verified HG decisions and full rankings for a paired comparison."""

import json
from collections import Counter
from types import SimpleNamespace

from app.generation.context import build_context
from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o.advanced_case_sources import ROLES
from experimental.u_10o0o.advanced_hg_partition_benchmark import (
    ARMS,
    _cohorts,
    full_fallback_result,
    full_fallback_trace,
    paired_full_fallback,
)
from experimental.u_10o0o.advanced_hg_relations import HGRelations
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    DEFAULT_CONTEXT_TOKENS,
    FINAL_K,
    OFFICIAL_SPLITS,
    PEER_AMBIGUOUS,
    evaluation_files,
    evaluation_questions,
)
from experimental.u_10o0o.advanced_target_benchmark import ranked_sources
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.context_evidence import (
    _context_candidates,
    _count_tokens,
    _source_key,
)
from experimental.u_10o0o.search import load_index


def _indexed_rows(path, fields):
    rows = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            key = tuple(row[field] for field in fields)
            if key in rows:
                raise ValueError(f"저장된 기록에 중복 행이 있습니다: {path.name}")
            rows[key] = row
    return rows


def _restore(row, page_by_source):
    def ranked(key):
        return [(page_by_source[_source_key(item)], 0.0) for item in row[key]]

    pool, expanded, packed = ranked("pool"), ranked("expanded"), ranked("packed")
    if len(pool) > CANDIDATES or len(packed) > FINAL_K:
        raise ValueError("저장된 기록이 후보 또는 문맥 쪽 수 상한을 넘었습니다.")
    context = build_context(
        _context_candidates(packed), FINAL_K, DEFAULT_CONTEXT_TOKENS, _count_tokens
    )
    if (context.context_hash, context.token_count) != (
        row["context_sha256"],
        row["context_tokens"],
    ):
        raise ValueError("저장된 최종 문맥 지문이 현재 복원 원본과 다릅니다.")
    return pool, expanded, context, (), row["route"]


def _verified_sources(trace, lookup, relations):
    linked = {tuple(key) for key in trace["database_sources"]}
    cases = {lookup.page_roles[key][0] for key in linked}
    if not cases or len(cases) != trace["resolved_case_count"]:
        raise ValueError("저장된 DB 조회의 사건과 출처가 일치하지 않습니다.")
    pages = {
        case: dict(zip(ROLES, relations.pages_for(case), strict=True)) for case in cases
    }
    if {
        _source_key(page) for roles in pages.values() for page in roles.values()
    } != linked:
        raise ValueError("현재 SQLite 원본이 저장된 출처와 다릅니다.")
    return pages, linked


def replay_hg_fallback(args, output, log):
    from experimental.u_10o0o.advanced_experiment import (
        artifact_path,
        experiment_config,
    )

    archive = args.replay_from.resolve()
    old_config = json.loads((archive / "config.json").read_bytes())
    files = evaluation_files("peer") if args.suite == "peer" else evaluation_files()
    if args.suite == "official":
        files = {split: files[split] for split in OFFICIAL_SPLITS}
    questions = evaluation_questions(files)
    expected = 304 if args.suite == "peer" else 60
    qids = {q["id"] for q in questions}
    if len(questions) != expected or len(qids) != expected:
        raise ValueError(f"재생에는 중복 없는 {expected}문항이 필요합니다.")
    config = experiment_config()
    config["question_inputs"] = {
        split: evaluation.stamp(q) for split, (q, _) in files.items()
    }
    relation = artifact_path(args.hg_relations_snapshot)
    config["relation_snapshot"] = evaluation.stamp(relation / "manifest.json")
    for key in ("inputs", "limits", "model", "question_inputs", "relation_snapshot"):
        if json.loads(json.dumps(config[key])) != old_config[key]:
            raise ValueError(f"저장된 비교와 현재 {key} 지문이 다릅니다.")
    config.update(
        suite=args.suite,
        replay_inputs={
            name: evaluation.stamp(archive / name)
            for name in (
                "config.json",
                "rankings.jsonl",
                "traces.jsonl",
                "scores.jsonl",
                "results.json",
            )
        },
        reused_luna_calls=json.loads((archive / "results.json").read_bytes())[
            "provider"
        ]["calls"],
    )
    if config["reused_luna_calls"] != expected:
        raise ValueError("저장된 실제 Luna 호출 수가 평가 문항 수와 다릅니다.")
    evaluation.write_json(output / "config.json", config)

    pages, _ = load_index()
    page_by_source = {_source_key(page): page for page in pages}
    lookup = load_target_lookup(pages)
    rankings = _indexed_rows(archive / "rankings.jsonl", ("id", "arm"))
    traces = _indexed_rows(archive / "traces.jsonl", ("id", "arm"))
    old_scores = _indexed_rows(archive / "scores.jsonl", ("id",))
    old_keys = {(qid, arm) for qid in qids for arm in ARMS[:-1]}
    if (
        set(rankings) != old_keys
        or set(traces) != old_keys
        or set(old_scores) != {(qid,) for qid in qids}
    ):
        raise ValueError("저장된 비교의 문항과 설정이 현재 재생 범위와 다릅니다.")

    retrievals, decisions = {}, {}
    log(f"기존 5:5 후보·검증된 Luna 분기 재사용 · {expected}문항 · 새 API 호출 0회")
    with HGRelations(
        relation,
        pages=pages,
        structured_snapshot=artifact_path(args.structured_snapshot),
    ) as relations:
        for row in questions:
            qid, question = row["id"], row["question"]
            base_key, partition_key = ((qid, arm) for arm in ARMS[:-1])
            original = _restore(rankings[base_key], page_by_source)
            partition = _restore(rankings[partition_key], page_by_source)
            previous = traces[partition_key]
            path = previous["route"]
            source_pages, roles, full_order = {}, {}, []
            if path in {"lookup", "mixed"}:
                source_pages, verified = _verified_sources(previous, lookup, relations)
            if path == "lookup":
                # Preserve the exact selected DB source order, including two-case lookups.
                if (
                    partition[0]
                    or not set(evaluation.packed_sources(partition[2])) <= verified
                ):
                    raise ValueError(
                        "저장된 DB 단독 문맥에 검색 후보나 확인되지 않은 출처가 있습니다."
                    )
            elif path == "mixed":
                if len(source_pages) != 1:
                    raise ValueError(
                        "이전 혼합 기록은 단일 확정 사건만 재생할 수 있습니다."
                    )
                roles = {case: ROLES for case in source_pages}
                full_order = [
                    (page_by_source[tuple(key)], 0.0)
                    for key in traces[base_key]["reranked"]
                ]
                pool_keys = evaluation.sources(original[0])
                if sorted(pool_keys) != sorted(evaluation.sources(full_order)):
                    raise ValueError("저장된 전체 KURE 순위가 5:5 후보와 다릅니다.")
            elif path not in {"general", "hg_search", "clarify"}:
                raise ValueError("저장된 실험에 알 수 없는 분기가 있습니다.")
            revised, new_path = full_fallback_result(
                question,
                path,
                original,
                full_order,
                partition,
                lookup,
                source_pages,
                roles,
            )
            retrievals.update(
                {base_key: original, partition_key: partition, (qid, ARMS[-1]): revised}
            )
            decisions[qid] = SimpleNamespace(path=path)
            traces[qid, ARMS[-1]] = full_fallback_trace(previous, revised, new_path)
            if new_path == "mixed":
                rankings[qid, ARMS[-1]] = {
                    "id": qid,
                    "arm": ARMS[-1],
                    "pool": ranked_sources(revised[0]),
                    "expanded": ranked_sources(revised[1]),
                    "packed": [
                        {"book_id": book, "page": page}
                        for book, page in evaluation.packed_sources(revised[2])
                    ],
                    "context_sha256": revised[2].context_hash,
                    "context_tokens": revised[2].token_count,
                    "linked_cases_count": len(revised[3]),
                    "route": revised[4],
                }
            else:
                reused = partition_key if new_path == "lookup" else base_key
                rankings[qid, ARMS[-1]] = {**rankings[reused], "arm": ARMS[-1]}

    log("세 경로의 검색·조회 출처와 문맥을 저장한 뒤 참고 근거 채점")
    _, scores, references = evaluation.score(
        output,
        questions,
        files,
        retrievals,
        traces,
        ARMS,
        ranking_rows=(rankings[q["id"], arm] for q in questions for arm in ARMS),
    )
    config["references"] = {
        split: evaluation.stamp(ref) for split, (_, ref) in files.items()
    }
    if config["references"] != old_config["references"]:
        raise ValueError("저장된 비교와 현재 참고 근거 지문이 다릅니다.")
    ambiguous = set()
    if args.suite == "peer":
        config["peer_ambiguous"] = evaluation.stamp(PEER_AMBIGUOUS)
        if config["peer_ambiguous"] != old_config["peer_ambiguous"]:
            raise ValueError("동료의 모호 표시 지문이 다릅니다.")
        ambiguous = set(json.loads(PEER_AMBIGUOUS.read_bytes()))
        config["evidence_group_policy"] = "any_citation_per_group"
    for row in scores:
        if any(
            row["arms"][arm] != old_scores[(row["id"],)]["arms"][arm]
            for arm in ARMS[:-1]
        ):
            raise ValueError("기존 두 설정의 재채점이 저장 점수와 다릅니다.")
    evaluation.write_json(output / "config.json", config)
    evaluation.write_json(
        output / "results.json",
        {
            "config": config,
            "cohorts": _cohorts(
                scores, questions, references, decisions, peer_ambiguous=ambiguous
            ),
            "full_fallback_routes": dict(
                Counter(
                    trace["route"]
                    for (_, arm), trace in traces.items()
                    if arm == ARMS[-1]
                )
            ),
            "paired_full_fallback": paired_full_fallback(scores),
            "validation": {
                "restored_context_hashes": len(old_keys),
                "unchanged_prior_scores": len(old_keys),
                "max_final_pages": max(
                    len(result[2].final_chunks) for result in retrievals.values()
                ),
                "max_final_tokens": max(
                    result[2].token_count for result in retrievals.values()
                ),
            },
        },
    )
    log(f"세 경로 재채점 완료 · {expected}문항 · 새 API 호출 0회")
