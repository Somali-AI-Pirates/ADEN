"""Benchmark exact HA and handbook record lookup independently."""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from statistics import fmean
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_references
from app.ingestion.curation import read_rows, write_rows
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_case_lookup import (
    ARCHIVE,
    METRICS,
    augment,
    rank_fielded,
    score,
)
from experimental.u_10o0o.advanced_case_sources import CURATED, hg_ids
from experimental.u_10o0o.advanced_fast_bm25 import FastBM25
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_pattern_full import BATCH_SIZE, DATA, input_files
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_role_context import PREVIOUS
from experimental.u_10o0o.advanced_search import (
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    fingerprint,
)
from experimental.u_10o0o.prepare import CACHE_PATH

HA_ROLES = ("계약 조건 확인", "사건·증빙 접수 대조", "비용별 정산 확인")
HANDBOOK = {
    frozenset(
        ("기존·요청 근거", "대상", "상태·기록일", "요청·회신 내용", "처리 기록")
    ): "처리 기록",
    frozenset(
        ("기록번호", "대상", "범위·상태", "업무·의제", "확인·회신 내용")
    ): "기록번호",
}
ARMS = ("original", "format_lookup")


def ha_ids(text):
    return {key for key in identifiers(text) if key.startswith("HA28-")}


def load_formats(curated):
    sources = {s["source_id"]: s for s in read_rows(curated / "sources.jsonl")}
    ha_sources = defaultdict(dict)
    role_by_source = {}
    handbook_sources = set()
    rare_sources = set()
    for sid, source in sources.items():
        heading = source["heading"]
        ids = ha_ids(heading)
        if ids:
            case, _, role = heading.rsplit(" > ", 1)[-1].partition(" ")
            if ids != {case} or role not in HA_ROLES or role in ha_sources[case]:
                raise ValueError("HA 사건의 머리말이나 역할이 모호합니다.")
            ha_sources[case][role] = source
            role_by_source[sid] = case, role
        elif re.match(r"IS\d+\s*\|", heading):
            handbook_sources.add(sid)
        elif any(key.startswith(("SW28-", "DL28-")) for key in identifiers(heading)):
            rare_sources.add(sid)
    if not ha_sources or any(
        set(roles) != set(HA_ROLES) for roles in ha_sources.values()
    ):
        raise ValueError("HA 사건마다 세 역할의 원본 쪽이 필요합니다.")

    ha_rows = defaultdict(list)
    handbook_rows = defaultdict(list)
    for path in sorted((curated / "records").glob("*.jsonl")):
        for row in read_rows(path):
            sid = row["source_id"]
            if sid in role_by_source:
                case, _ = role_by_source[sid]
                if row["kind"] == "table_row" and ha_ids(
                    " ".join(row.get("case_ids", ()))
                ) != {case}:
                    raise ValueError("HA 표 행의 사건이 원본 머리말과 다릅니다.")
                ha_rows[sid].append(row)
            elif (
                sid in handbook_sources
                and row["kind"] == "table_row"
                and row.get("review_status") == "parsed"
            ):
                label = HANDBOOK.get(frozenset(row["fields"]))
                if label and len(keys := identifiers(row["fields"][label])) == 1:
                    handbook_rows[keys[0]].append((label, row))
    if set(ha_rows) != set(role_by_source):
        raise ValueError("HA 사건에서 원문 기록이 없는 역할이 있습니다.")
    return sources, ha_sources, ha_rows, handbook_rows, rare_sources


def candidate(question, ha_pages, handbook_pages):
    if hg_ids(question):
        return None
    keys = set(identifiers(question))
    ha = ha_ids(question)
    records = keys & handbook_pages.keys()
    if len(ha) == 1 and not records:
        key = next(iter(ha))
        if key in ha_pages:
            return "ha", key, ha_pages[key]
    if not ha and len(records) == 1:
        key = next(iter(records))
        return "handbook", key, [handbook_pages[key]]
    return None


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        split = "overlap" if row["blind_related"] else "disjoint"
        groups["all"].append(row)
        groups[split].append(row)
        groups[f"{split}:type:{row['question_type']}"].append(row)
        groups[f"{split}:route:{row['route']}"].append(row)
    return {
        name: {
            "questions": len(items),
            "routes": dict(Counter(r["route"] for r in items)),
            "changed_contexts": sum(r["changed_context"] for r in items),
            "linked_to_reference": sum(r["linked_to_reference"] for r in items),
            "wrong_ha_cases": sum(r["wrong_ha_case"] for r in items),
            "archived_control_differences": sum(
                r["archived_control_differs"] for r in items
            ),
            "gained_all_facts": sum(
                r["arms"][ARMS[1]]["all_facts"] > r["arms"][ARMS[0]]["all_facts"]
                for r in items
            ),
            "lost_all_facts": sum(
                r["arms"][ARMS[1]]["all_facts"] < r["arms"][ARMS[0]]["all_facts"]
                for r in items
            ),
            "arms": {
                arm: {key: fmean(r["arms"][arm][key] for r in items) for key in METRICS}
                for arm in ARMS
            },
        }
        for name, items in groups.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-index", type=Path, required=True)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise SystemExit("기준선과 같은 CUDA FP16 환경이 필요합니다.")
    started = perf_counter()
    questions, inputs = input_files()
    archive = json.loads((ARCHIVE / "config.json").read_bytes())
    previous = json.loads((PREVIOUS / "config.json").read_bytes())
    manifest = (args.curated / "manifest.json").read_bytes()
    if (
        inputs != previous["inputs"]
        or fingerprint(CACHE_PATH) != previous["pages"]
        or sha256(manifest).hexdigest()
        != previous["case_index"]["curation_manifest_sha256"]
        or previous["kure"] != archive["kure"]
        or previous["kure"]["gpu"] != torch.cuda.get_device_name()
    ):
        raise ValueError("보관 기준선과 입력 또는 실행 환경이 다릅니다.")

    sources, ha, ha_rows, handbook, rare = load_formats(args.curated)
    pages = json.loads(CACHE_PATH.read_bytes())
    page_index = {location(page): page for page in pages}
    ha_pages = {
        case: [
            page_index[(roles[role]["book_id"], roles[role]["page"])]
            for role in HA_ROLES
        ]
        for case, roles in ha.items()
    }
    handbook_pages = {}
    for key, rows in handbook.items():
        if len(rows) == 1:
            source = sources[rows[0][1]["source_id"]]
            handbook_pages[key] = page_index[(source["book_id"], source["page"])]

    decisions = {
        q["id"]: found
        for q in questions
        if (found := candidate(q["question"], ha_pages, handbook_pages))
    }
    active = [q for q in questions if q["id"] in decisions]
    args.output.mkdir(parents=True, exist_ok=False)
    args.local_index.mkdir(parents=True, exist_ok=False)
    write_rows(
        args.local_index / "ha_pages.jsonl",
        (
            {
                "case_id": case,
                "record_role": role,
                "source": source,
                "table_rows": [
                    row
                    for row in ha_rows[source["source_id"]]
                    if row["kind"] == "table_row"
                ],
            }
            for case, roles in sorted(ha.items())
            for role, source in sorted(roles.items())
        ),
    )
    write_rows(
        args.local_index / "handbook_rows.jsonl",
        (
            {"record_key": key, "key_column": label, "record": row}
            for key, rows in sorted(handbook.items())
            if key in handbook_pages
            for label, row in rows
        ),
    )

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (args.output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    log(
        f"양식 조회 시작 · HA {len(ha):,}사건 · 자료집 {len(handbook_pages):,}키 · 대상 {len(active):,}문항"
    )
    write_json(
        args.output / "config.json",
        {
            **{
                key: previous[key]
                for key in (
                    "inputs",
                    "pages",
                    "candidate_count",
                    "final_k",
                    "context_tokens",
                    "kure",
                )
            },
            "curation_manifest_sha256": sha256(manifest).hexdigest(),
            "previous_results": fingerprint(PREVIOUS / "questions.jsonl"),
            "ha_cases": len(ha),
            "ha_source_pages": len(ha_rows),
            "handbook_keys": len(handbook_pages),
            "handbook_rows_by_key_column": dict(
                Counter(
                    rows[0][0]
                    for key, rows in handbook.items()
                    if key in handbook_pages
                )
            ),
            "ambiguous_handbook_keys": len(handbook) - len(handbook_pages),
            "rare_source_pages_using_original": len(rare),
            "generation_calls": 0,
        },
    )
    index = PatternIndex(pages)
    if json.loads(json.dumps(index.config)) != archive["retrieval"]:
        raise ValueError("제목 보너스 검색 설정이 다릅니다.")
    index.base_index = FastBM25(index.base_index)
    if index.heading_index is not None:
        index.heading_index = FastBM25(index.heading_index)
    reranker = CrossEncoder(
        KURE,
        revision=MODELS[KURE],
        device="cuda",
        max_length=MAX_TOKENS,
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    packed = {}
    for start in range(0, len(active), BATCH_SIZE):
        batch = active[start : start + BATCH_SIZE]
        rankings = rank_fielded(index, reranker, batch, log)
        for q, order in zip(batch, rankings, strict=True):
            linked = decisions[q["id"]][2]
            expanded = augment(order, linked)
            packed[q["id"]] = (
                order,
                packed_context(order),
                expanded,
                packed_context(expanded),
            )
        log(f"두 조건 문맥 준비 · {len(packed):,}/{len(active):,}문항")

    references = load_references(DATA / "references.json")
    previous_rows = {r["id"]: r for r in read_rows(PREVIOUS / "questions.jsonl")}
    expected_ids = {q["id"] for q in questions}
    if expected_ids != set(references) or expected_ids != set(previous_rows):
        raise ValueError("질문·참고 파일·보관 결과의 ID가 다릅니다.")
    rows = []
    for q in questions:
        qid = q["id"]
        ref, old = references[qid], previous_rows[qid]
        archived = old["arms"]["indirect_lookup"]
        base = result = archived
        route, changed, correct, wrong = "fallback", False, False, False
        if qid in decisions:
            if old["route"] in {"explicit_hg", "triggered"}:
                raise ValueError(
                    "HA 또는 자료집 조회와 보관된 HG 조회 대상이 겹칩니다."
                )
            route, key, linked = decisions[qid]
            order, full, expanded, added = packed[qid]
            base = score(q, order, full, ref)
            result = score(q, expanded, added, ref)
            changed = full.context_hash != added.context_hash
            evidence = {
                (item["book_id"], item["page"])
                for group in ref["evidence_groups"]
                for item in group
            }
            correct = bool(evidence) and {location(page) for page in linked} == evidence
            wrong = route == "ha" and key != ref["case_id"]
        rows.append(
            {
                "id": qid,
                "blind_related": old["blind_related"],
                "question_type": ref["question_type"],
                "identifier_mode": ref["identifier_mode"],
                "route": route,
                "changed_context": changed,
                "linked_to_reference": correct,
                "wrong_ha_case": wrong,
                "archived_control_differs": base != archived,
                "arms": dict(zip(ARMS, (base, result), strict=True)),
            }
        )
    write_rows(args.output / "questions.jsonl", rows)
    write_json(args.output / "summary.json", summarize(rows))
    log(f"6,500문항 평가 완료 · 경과 {perf_counter() - started:.1f}초 · 외부 API 0회")


if __name__ == "__main__":
    main()
