"""Build snapshots and compare local Advanced search and routing variants."""

import argparse
import json
import sqlite3
from collections.abc import Mapping
from contextlib import ExitStack
from copy import copy
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import perf_counter

from experimental.u_10o0o import advanced_experiment_eval as evaluation
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES
from experimental.u_10o0o.advanced_clarify_lookup import load_storage_lookup
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX, location
from experimental.u_10o0o.advanced_role_context import FACTS
from experimental.u_10o0o.advanced_search import (
    CANDIDATES,
    DEFAULT_CONTEXT_TOKENS,
    FINAL_K,
    HYBRID_WEIGHTS,
    KST,
    KURE,
    MODELS,
    OFFICIAL_SPLITS,
    RERANKED_HEAD,
    RRF_K,
    evaluation_files,
    evaluation_questions,
    retain_reranked_head,
)
from experimental.u_10o0o.advanced_target_lookup import load_target_lookup
from experimental.u_10o0o.embeding import MODEL_ID, MODEL_REVISION, VECTORS_PATH
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT
from experimental.u_10o0o.search import load_index

ARTIFACTS = PROJECT_ROOT / "artifacts"
LOCAL = ARTIFACTS / "u_10o0o"
IS_SNAPSHOT = LOCAL / "advanced-snapshots/is-snowflake-v1"
IS_TABLE_SNAPSHOT = LOCAL / "advanced-snapshots/is-table-rendered-v1"
HG_SNAPSHOT = LOCAL / "advanced-snapshots/hg-relations-v1"
HG_RULE_SNAPSHOT = LOCAL / "advanced-snapshots/hg-rule-occurrences-v1"
STRUCTURED_SNAPSHOT = LOCAL / "advanced-snapshots/structured-v1"
HG_CODE_SNAPSHOT = LOCAL / "advanced-snapshots/hg-code-relations-v1"
TYPED_GOLD = LOCAL / "advanced-structured-gold-20261109"
RULES_DB = LOCAL / "advanced-routing/routing_rules.sqlite3"


def artifact_path(path):
    path = path.resolve()
    if not path.is_relative_to(ARTIFACTS) or path == ARTIFACTS:
        raise ValueError("로컬 산출물은 저장소 artifacts 하위에 저장하세요.")
    return path


def start_run(path):
    output = artifact_path(path)
    output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    return output, log


class SQLiteCasePages(Mapping):
    """Keep identity checks in memory and read selected role pages from SQLite."""

    def __init__(self, keys, store):
        self.keys_by_case = keys
        self.store = store

    def __iter__(self):
        return iter(self.keys_by_case)

    def __len__(self):
        return len(self.keys_by_case)

    def __contains__(self, key):
        return key in self.keys_by_case

    def __getitem__(self, key):
        return self.pages_for(key, ROLES)

    def pages_for(self, key, roles):
        if key not in self.keys_by_case:
            raise KeyError(key)
        pages = self.store.pages_for(key, roles)
        if len(pages) != len(roles):
            raise ValueError("검증된 HG 사건의 SQLite 역할 연결이 불완전합니다.")
        return pages


def measure_hg(memory, sqlite_pages):
    keys = list(memory)
    for key in keys[:20]:
        memory[key], sqlite_pages[key]
    timings = {arm: [] for arm in ("memory", "sqlite")}
    matches = 0
    for key in keys:
        start = perf_counter()
        expected = memory[key]
        timings["memory"].append((perf_counter() - start) * 1000)
        start = perf_counter()
        actual = sqlite_pages[key]
        timings["sqlite"].append((perf_counter() - start) * 1000)
        if list(map(location, expected)) != list(map(location, actual)):
            raise ValueError("SQLite 역할·쪽·순서가 기존 HG 인덱스와 다릅니다.")
        matches += len(actual)
    return {
        "matched_cases": len(keys),
        "matched_roles": matches,
        "warm_lookup": {
            arm: evaluation.latency(values) for arm, values in timings.items()
        },
    }


def experiment_config():
    return {
        "limits": {
            "candidates": CANDIDATES,
            "context_pages": FINAL_K,
            "context_tokens": DEFAULT_CONTEXT_TOKENS,
        },
        "inputs": {
            "pages": evaluation.stamp(CACHE_PATH),
            "vectors": evaluation.stamp(VECTORS_PATH),
            "curated": evaluation.stamp(CURATED / "manifest.json"),
            "cases": evaluation.stamp(CASE_INDEX / "case_sources.jsonl"),
            "facts": evaluation.stamp(FACTS / "role_facts.jsonl"),
        },
        "model": {
            "embedding": MODEL_ID,
            "embedding_revision": MODEL_REVISION,
            "reranker": KURE,
            "reranker_revision": MODELS[KURE],
            "reranked_head": RERANKED_HEAD,
            "rrf_weights": HYBRID_WEIGHTS,
            "rrf_k": RRF_K,
        },
        "external_api_calls": 0,
    }


def warm_request_timings(search, questions, variants):
    samples = questions[:: max(1, len(questions) // 12)][:12]
    search.one(samples[0]["question"])
    times = {name: {"lookup": [], "end_to_end": []} for name in variants}
    search_times = []
    for question in samples:
        text = question["question"]
        started = perf_counter()
        pool, order = search.one(text)
        search_ms = (perf_counter() - started) * 1000
        search_times.append(search_ms)
        for arm, (lookup, storage) in variants.items():
            started = perf_counter()
            evaluation.finish(text, pool, order, lookup, storage)
            lookup_ms = (perf_counter() - started) * 1000
            times[arm]["lookup"].append(lookup_ms)
            times[arm]["end_to_end"].append(search_ms + lookup_ms)
    return {
        "sample_ids": [row["id"] for row in samples],
        "common_search": evaluation.latency(search_times),
        "arms": {
            arm: {
                stage: evaluation.latency(values) for stage, values in by_stage.items()
            }
            for arm, by_stage in times.items()
        },
    }


def automatic_cases(trace, plan):
    hg_route, source_route, *_ = trace["route"].split("/")
    cases = set(trace["cases"]) if source_route in ("unique", "pair") else set()
    if hg_route in ("explicit_hg", "indirect_hg") and trace["roles"]:
        cases.add(trace["roles"][0][0])
    if plan["status"] in ("explicit", "composite"):
        cases.update(target["case"] for target in plan["targets"])
    return cases


def rule_regressions(questions, refs, traces, scores):
    losses = []
    for question, score in zip(questions, scores, strict=True):
        qid = question["id"]
        reference = refs[question["split"]][qid]
        prior = traces[qid, "baseline"]
        after = traces[qid, "rules"]
        gold = set(reference.get("case_keys", ()))
        if reference.get("expected_action") in ("clarify", "needs_clarification"):
            gold.clear()
        wrong_before = automatic_cases(prior, prior["interpretation"]) - gold
        wrong_after = automatic_cases(after, after["interpretation"]) - gold
        if (
            wrong_after - wrong_before
            or score.get("final_delta", {}).get("rules") == -1
        ):
            losses.append(qid)
    return losses


def compare_core(
    kind,
    output,
    log,
    *,
    is_snapshot=IS_SNAPSHOT,
    hg_snapshot=HG_SNAPSHOT,
    matcher=None,
    baseline_rules=None,
):
    started = perf_counter()
    files = evaluation_files("categories")
    if kind == "is":
        standard = evaluation_files()
        files.update({split: standard[split] for split in OFFICIAL_SPLITS})
    questions = evaluation_questions(files)
    if len({question["id"] for question in questions}) != len(questions):
        raise ValueError("비교 문항 ID가 중복됩니다.")
    pages, base = load_index()
    before_lookup = perf_counter()
    lookup = load_target_lookup(pages, rules=baseline_rules)
    storage = load_storage_lookup(pages)
    lookup_load_ms = (perf_counter() - before_lookup) * 1000
    config = experiment_config()
    config["evaluation"] = {
        split: {"questions": evaluation.stamp(q), "references": evaluation.stamp(r)}
        for split, (q, r) in files.items()
    }
    measures = {
        "baseline_lookup_load_ms": lookup_load_ms,
        "rss_after_common_load_bytes": evaluation.rss_bytes(),
    }
    search = evaluation.Search(pages, base)
    arms = ("baseline", kind)
    variants = dict.fromkeys(arms, (lookup, storage))
    vectors = {"baseline": base}
    retrievals, traces = {}, {}
    log(f"{kind} 비교 시작 · {len(questions)}문항 · {len(pages):,}쪽")
    with ExitStack() as stack:
        if kind == "is":
            from experimental.u_10o0o.advanced_snapshots import load_is

            vectors[kind] = load_is(is_snapshot, pages, base)
            config["snapshot"] = evaluation.stamp(is_snapshot / "manifest.json")
        elif kind == "hg":
            from experimental.u_10o0o.advanced_snapshots import HGStore

            start = perf_counter()
            hg = stack.enter_context(HGStore(hg_snapshot, pages))
            case_pages = SQLiteCasePages(lookup.cases, hg)
            sqlite_lookup, sqlite_storage = copy(lookup), copy(storage)
            sqlite_lookup.cases = sqlite_storage.cases = case_pages
            variants[kind] = sqlite_lookup, sqlite_storage
            measures["sqlite_load_ms"] = (perf_counter() - start) * 1000
            measures["rss_after_sqlite_load_bytes"] = evaluation.rss_bytes()
            measures["sqlite_bytes"] = (hg_snapshot / "advanced.sqlite3").stat().st_size
            measures["baseline_relation_jsonl_bytes"] = (
                (CASE_INDEX / "case_sources.jsonl").stat().st_size
            )
            measures.update(measure_hg(lookup.cases, case_pages))
            config["snapshot"] = evaluation.stamp(hg_snapshot / "manifest.json")
            log(f"HG 역할 조회 {measures['matched_roles']:,}개 일치")
        else:
            start = perf_counter()
            rule_lookup = copy(lookup)
            rule_lookup.rules = matcher
            variants[kind] = rule_lookup, storage
            config["rules"] = {
                "version": matcher.version_id,
                "sha256": matcher.content_sha256,
                "baseline_version": baseline_rules.version_id
                if baseline_rules
                else None,
            }
            measures["rule_lookup_setup_ms"] = (perf_counter() - start) * 1000
        for batch in evaluation.batches(questions):
            pools, heads = search.rank(batch, vectors, log)
            for arm, (active_lookup, active_storage) in variants.items():
                ranking_arm = arm if arm in pools else "baseline"
                for question, pool, head in zip(
                    batch, pools[ranking_arm], heads[ranking_arm], strict=True
                ):
                    qid, text = question["id"], question["question"]
                    order = retain_reranked_head(pool, head)
                    result, linked = evaluation.finish(
                        text, pool, order, active_lookup, active_storage
                    )
                    retrievals[qid, arm] = result
                    trace = evaluation.trace(result, linked, active_lookup, order)
                    if kind == "rules":
                        trace["interpretation"] = evaluation.query_plan(
                            text, active_lookup
                        )
                    traces[qid, arm] = trace
        measures["rss_after_search_bytes"] = evaluation.rss_bytes()
        if kind == "hg":
            measures["warm_requests"] = warm_request_timings(
                search, questions, variants
            )
    summary, rows, refs = evaluation.score(
        output, questions, files, retrievals, traces, arms
    )
    changed = [
        q["id"]
        for q in questions
        if traces[q["id"], "baseline"] != traces[q["id"], kind]
    ]
    regressions = (
        rule_regressions(questions, refs, traces, rows) if kind == "rules" else []
    )
    results = {
        "kind": kind,
        "config": config,
        "summary": summary,
        "measurements": measures,
        "identical_traces": len(questions) - len(changed),
        "changed_questions": changed,
        "rule_regressions": regressions,
        "final_gains": [
            row["id"] for row in rows if row.get("final_delta", {}).get(kind) == 1
        ],
        "final_losses": [
            row["id"] for row in rows if row.get("final_delta", {}).get(kind) == -1
        ],
        "elapsed_seconds": perf_counter() - started,
    }
    evaluation.write_json(output / "results.json", results)
    log(f"{kind} 채점 완료 · 동일 추적 {results['identical_traces']}/{len(questions)}")
    if kind == "hg" and changed:
        raise ValueError("HG 저장층 변경 후 문항별 조회 결과가 달라졌습니다.")
    return results


def proposal_rules(snapshot, proposal, author_kind):
    from experimental.u_10o0o.advanced_routing_rules import RoutingRule, normalize

    if not isinstance(proposal, dict) or proposal.keys() - {"add", "disable"}:
        raise ValueError("규칙 제안은 add·disable 목록을 가진 JSON 객체여야 합니다.")
    additions = proposal.get("add", [])
    removals = proposal.get("disable", [])
    if not isinstance(additions, list) or not isinstance(removals, list):
        raise ValueError("규칙 제안의 add·disable은 목록이어야 합니다.")
    if not all(isinstance(item, str) for item in removals):
        raise ValueError("비활성화 대상에는 규칙 ID를 사용하세요.")
    disabled = set(removals)
    if not (additions or disabled) or (disabled and author_kind == "llm"):
        raise ValueError(
            "관리자는 추가·비활성화, LLM 제안은 새 표현 추가만 할 수 있습니다."
        )
    if disabled - {row.rule_id for row in snapshot.rules if row.enabled}:
        raise ValueError("비활성화할 수 있는 활성 규칙 ID를 지정하세요.")
    rows = [
        replace(row, enabled=False) if row.rule_id in disabled else row
        for row in snapshot.rules
    ]
    for item in additions:
        if not isinstance(item, dict) or set(item) != {
            "scope",
            "target_code",
            "phrase",
        }:
            raise ValueError("추가할 표현에는 scope·target_code·phrase가 필요합니다.")
        if not all(isinstance(value, str) for value in item.values()):
            raise ValueError("표현 규칙 필드에는 문자열을 입력하세요.")
        scope = item["scope"]
        rows.append(
            RoutingRule(
                scope=scope,
                target_code=item["target_code"],
                match_mode="literal_phrase",
                pattern=normalize(item["phrase"]),
                position=1
                + max((row.position for row in rows if row.scope == scope), default=-1),
            )
        )
    return rows


def manage_rules(args):
    from experimental.u_10o0o.advanced_routing_rules import RoutingRuleStore

    store = RoutingRuleStore(artifact_path(args.db))
    if args.action == "seed":
        print(store.initialize())
    elif args.action == "draft":
        active = store.current_activation()
        if active is None:
            raise ValueError("규칙을 초기 검증하고 활성화한 뒤 초안을 만드세요.")
        rows = proposal_rules(
            store.snapshot(active.version_id),
            json.loads(args.proposal.read_bytes()),
            args.author_kind,
        )
        print(
            store.create_draft(
                rows,
                author_kind=args.author_kind,
                created_by=args.actor,
                base_version_id=active.version_id,
                base_activation_id=active.activation_id,
            )
        )
    elif args.action == "validate":
        output, log = start_run(args.output_dir)
        active = store.current_activation()
        baseline = store.snapshot(active.version_id) if active else None

        def reporter(draft):
            results = compare_core(
                "rules", output, log, matcher=draft, baseline_rules=baseline
            )
            report = {
                "passed": not results["rule_regressions"]
                and (active is not None or not results["changed_questions"]),
                "version_id": draft.version_id,
                "content_sha256": draft.content_sha256,
                "base_activation_id": active.activation_id if active else None,
                "results": evaluation.stamp(output / "results.json"),
                "changed_questions": results["changed_questions"],
                "regressions": results["rule_regressions"],
            }
            (output / "validation-report.json").write_bytes(
                json.dumps(
                    report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )
            return report

        print(store.validate_draft(args.version, reporter))
    else:
        operation = store.activate if args.action == "activate" else store.rollback
        result = operation(
            args.version,
            actor=args.actor,
            reason=args.reason,
            expected_activation_id=args.expect_activation,
        )
        print(result)


def run_compare(args):
    if args.suite == "peer" and args.kind != "hg-partition":
        raise ValueError("--suite peer는 hg-partition 비교에서 지원합니다.")
    if args.replay_from and (args.kind != "hg-partition" or args.live_luna):
        raise ValueError("--replay-from은 hg-partition의 오프라인 비교에서 지원합니다.")
    output, log = start_run(args.output_dir)
    try:
        if args.kind == "hg-partition":
            if args.replay_from:
                from experimental.u_10o0o.advanced_hg_fallback_replay import (
                    replay_hg_fallback,
                )

                replay_hg_fallback(args, output, log)
                return
            from experimental.u_10o0o.advanced_hg_partition_benchmark import (
                compare_hg_partition,
            )

            compare_hg_partition(args, output, log)
            return
        if args.kind == "structured":
            from experimental.u_10o0o.advanced_structured_benchmark import (
                compare_structured,
            )

            compare_structured(args, output, log)
            return
        if args.kind in ("is-tables", "hg-rule-index"):
            from experimental.u_10o0o import advanced_structured_curation

            compare = (
                advanced_structured_curation.compare_is_tables
                if args.kind == "is-tables"
                else advanced_structured_curation.compare_hg_rules
            )
            compare(args, output, log)
            return
        if args.kind == "luna":
            from experimental.u_10o0o.advanced_luna_benchmark import compare_luna

            compare_luna(args, output, log)
            return
        if args.kind in ("interpret", "lookup-first"):
            from experimental.u_10o0o.advanced_experiment_routes import compare_routes

            compare_routes(args, output, log)
            return
        matcher = None
        if args.kind == "rules":
            from experimental.u_10o0o.advanced_routing_rules import RoutingRuleStore

            matcher = RoutingRuleStore(artifact_path(args.rules_db)).snapshot(
                args.rule_version
            )
        compare_core(
            args.kind,
            output,
            log,
            is_snapshot=artifact_path(args.is_snapshot),
            hg_snapshot=artifact_path(args.hg_snapshot),
            matcher=matcher,
        )
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
        evaluation.write_json(
            output / "failure.json", {"error_type": type(error).__name__}
        )
        log(f"비교 실패 · {type(error).__name__}")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_is = commands.add_parser("build-is", help="IS 정제 벡터 스냅샷 생성")
    build_is.add_argument("--output", type=Path, default=IS_SNAPSHOT)
    build_is.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    build_tables = commands.add_parser("build-is-tables", help="IS 표 렌더링 벡터 생성")
    build_tables.add_argument("--output", type=Path, default=IS_TABLE_SNAPSHOT)
    build_tables.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    build_hg = commands.add_parser("build-hg", help="HG 관계 SQLite 스냅샷 생성")
    build_hg.add_argument("--output", type=Path, default=HG_SNAPSHOT)
    build_structured = commands.add_parser(
        "build-structured", help="HG·HA·IS 구조화 SQLite 생성"
    )
    build_structured.add_argument("--output", type=Path, default=STRUCTURED_SNAPSHOT)
    build_hg_codes = commands.add_parser(
        "build-hg-codes", help="HG 코드·사건·원본 관계 SQLite 생성"
    )
    build_hg_codes.add_argument("--output", type=Path, default=HG_CODE_SNAPSHOT)
    build_hg_codes.add_argument(
        "--structured-snapshot", type=Path, default=STRUCTURED_SNAPSHOT
    )
    rules = commands.add_parser("rules", help="표현 규칙 버전 관리")
    rules.add_argument("--db", type=Path, default=RULES_DB)
    actions = rules.add_subparsers(dest="action", required=True)
    actions.add_parser("seed")
    draft = actions.add_parser("draft")
    draft.add_argument("--proposal", type=Path, required=True)
    draft.add_argument("--author-kind", choices=("admin", "llm"), required=True)
    draft.add_argument("--actor", required=True)
    validate = actions.add_parser("validate")
    validate.add_argument("--version", required=True)
    validate.add_argument("--output-dir", type=Path, required=True)
    for action in ("activate", "rollback"):
        operation = actions.add_parser(action)
        operation.add_argument("--version", required=True)
        operation.add_argument("--actor", required=True)
        operation.add_argument("--reason", required=True)
        operation.add_argument("--expect-activation")

    compare = commands.add_parser("compare", help="한 변경군과 대조군 비교")
    compare.add_argument(
        "kind",
        choices=(
            "is",
            "hg",
            "rules",
            "interpret",
            "lookup-first",
            "luna",
            "structured",
            "is-tables",
            "hg-rule-index",
            "hg-partition",
        ),
    )
    compare.add_argument("--output-dir", type=Path, required=True)
    compare.add_argument(
        "--replay-from", type=Path, help="HG 분리 비교의 기존 두 설정 기록 경로"
    )
    compare.add_argument(
        "--suite",
        choices=("official", "peer"),
        default="official",
        help="HG 분리 비교의 평가 묶음",
    )
    compare.add_argument("--is-snapshot", type=Path, default=IS_SNAPSHOT)
    compare.add_argument("--is-table-snapshot", type=Path, default=IS_TABLE_SNAPSHOT)
    compare.add_argument("--hg-snapshot", type=Path, default=HG_SNAPSHOT)
    compare.add_argument("--hg-rule-snapshot", type=Path, default=HG_RULE_SNAPSHOT)
    compare.add_argument("--hg-relations-snapshot", type=Path, default=HG_CODE_SNAPSHOT)
    compare.add_argument("--rules-db", type=Path, default=RULES_DB)
    compare.add_argument("--rule-version")
    compare.add_argument("--fixtures", type=Path)
    compare.add_argument(
        "--structured-snapshot", type=Path, default=STRUCTURED_SNAPSHOT
    )
    compare.add_argument("--typed-gold", type=Path, default=TYPED_GOLD)
    compare.add_argument(
        "--live-luna",
        action="store_true",
        help="구조화·HG 분리 비교에서 실제 Luna API 사용",
    )
    args = parser.parse_args()
    try:
        if args.command == "rules":
            manage_rules(args)
        elif args.command == "compare":
            run_compare(args)
        elif args.command == "build-structured":
            from experimental.u_10o0o.advanced_structured_store import (
                build_structured_store,
            )

            print(build_structured_store(artifact_path(args.output)))
        elif args.command == "build-hg-codes":
            from experimental.u_10o0o.advanced_hg_relations import build_hg_relations

            print(
                build_hg_relations(
                    artifact_path(args.output),
                    structured_snapshot=artifact_path(args.structured_snapshot),
                )
            )
        elif args.command == "build-is-tables":
            from experimental.u_10o0o.advanced_structured_curation import (
                build_is_table_snapshot,
            )

            print(
                build_is_table_snapshot(artifact_path(args.output), device=args.device)
            )
        else:
            from experimental.u_10o0o import advanced_snapshots

            output = artifact_path(args.output)
            if args.command == "build-is":
                print(advanced_snapshots.build_is(output, device=args.device))
            else:
                print(advanced_snapshots.build_hg(output))
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
