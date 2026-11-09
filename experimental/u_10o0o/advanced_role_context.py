"""Compare full HG pages with source-line table and condition excerpts."""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import file_digest, sha256
from pathlib import Path
from statistics import fmean
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder

from app.evaluation.dataset import load_references
from app.ingestion.curation import read_rows, write_rows
from app.ingestion.layout import compact, header_cells, page_body, readable
from experimental.u_10o0o.advanced_case_lookup import (
    ARCHIVE,
    METRICS,
    rank_fielded,
    score,
)
from experimental.u_10o0o.advanced_case_sources import CURATED, ROLES
from experimental.u_10o0o.advanced_fast_bm25 import FastBM25
from experimental.u_10o0o.advanced_field_roles import PatternIndex
from experimental.u_10o0o.advanced_hg_lookup import (
    CASE_INDEX,
    ProfileIndex,
    augment,
    linked_pages,
    load_cases,
    load_profiles,
    matched_case,
)
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_pattern_full import BATCH_SIZE, DATA, input_files
from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_search import (
    KST,
    KURE,
    MAX_TOKENS,
    MODELS,
    RECORDS_DIR,
    fingerprint,
)
from experimental.u_10o0o.context_evidence import _count_tokens
from experimental.u_10o0o.prepare import CACHE_PATH

PREVIOUS = RECORDS_DIR / "advanced-curation/hg-indirect-lookup-20261109"
FACTS = Path("artifacts/u_10o0o/advanced-curation/hg-role-facts-20261109")
LAYOUT = Path("artifacts/u_10o0o/pattern-layout-cache/advanced")
CONDITION_NOTE = re.compile(
    r"조건|효력|정정|예외|증빙|누락|미결|미확정|승인|요청|반려|환급|충당|산입|"
    r"적용|기준|상한|감액|면제|각주|비고|참고|유의|오기|지정 외|수신|인계"
)
ARMS = ("full_page", "source_excerpt")


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def excerpt(raw, source, records, row_ids, rule_ids, page_count):
    """Keep exact source lines, with a visible gap between disjoint fragments."""
    try:
        heading, parts = page_body(raw, source["book_id"], source["page"], page_count)
    except ValueError:
        return None
    if heading != source["heading"] or not parts:
        return None
    spans = {(r["line_start"], r["line_end"]): r for r in records}
    contexts = [r for r in records if r["kind"] == "case_context"]
    if len(contexts) != 1:
        return None
    kept = set(range(parts[0]["start"] - 1))
    found_rows, found_rules = set(), set()
    found_context = False
    header_active = False
    for i, part in enumerate(parts):
        span = part["start"], part["end"]
        original = "\n".join(part["lines"])
        record = spans.get(span)
        rid = f"advanced.rule.{sha256(compact(original).encode()).hexdigest()[:16]}"
        if record and record["kind"] == "table_row":
            if not header_active or record["record_id"] not in row_ids:
                return None
            found_rows.add(record["record_id"])
        elif header_cells(part):
            header_active = True
        else:
            header_active = False
            if record and record["kind"] == "case_context":
                found_context = True
            elif rid in rule_ids:
                found_rules.add(rid)
            elif record and record["kind"] == "narrative":
                if not CONDITION_NOTE.search(readable(original)):
                    continue
            elif not (
                readable(original) in source.get("sections", ())
                and i + 1 < len(parts)
                and header_cells(parts[i + 1])
            ):
                return None
        kept.update(range(span[0] - 1, span[1]))
    if not found_context or found_rows != row_ids or found_rules != rule_ids:
        return None

    lines = raw.splitlines()
    rendered = []
    previous = -1
    for i in sorted(kept):
        if not lines[i].strip():
            continue
        if any(line.strip() for line in lines[previous + 1 : i]):
            rendered.append("…")
        rendered.append(readable(lines[i]))
        previous = i
    return "\n".join(rendered)


def prepare_views(curated, layout, facts_dir, cases, manifest, snapshot_sha):
    fact_summary = json.loads((facts_dir / "summary.json").read_bytes())
    fact_path = facts_dir / "role_facts.jsonl"
    if fact_summary["curation_manifest_sha256"] != snapshot_sha or fact_summary[
        "role_facts_sha256"
    ] != digest(fact_path):
        raise ValueError("역할별 필드와 정제 스냅샷의 지문이 다릅니다.")
    page_index = {}
    for case, pages in cases.items():
        for role, page in zip(ROLES, pages, strict=True):
            book, number = location(page)
            page_index[f"advanced:{book}:p{number}"] = case, role, page

    rows_by_source, rules_by_source = defaultdict(set), defaultdict(set)
    for fact in read_rows(fact_path):
        source = fact["source"]
        sid = source["source_id"]
        case, role, page = page_index[sid]
        if (
            fact["case_id"],
            fact["record_role"],
            source["file_name"],
            source["page"],
        ) != (case, role, page["file_name"], page["page"]):
            raise ValueError("정규화 필드의 사건·역할·출처가 인덱스와 다릅니다.")
        rows_by_source[sid].add(source["record_id"])
        rules_by_source[sid].update(source["page_rule_refs"])
    if set(rows_by_source) != set(page_index):
        raise ValueError("역할별 필드에서 누락된 사건 쪽이 있습니다.")
    confirmed_rules = {
        r["rule_id"]: set(r["sources"]) for r in read_rows(curated / "rules.jsonl")
    }
    if any(
        sid not in confirmed_rules[rid]
        for sid, rules in rules_by_source.items()
        for rid in rules
    ):
        raise ValueError("같은 출처에서 확인한 규칙만 쓸 수 있습니다.")
    source_by_book = defaultdict(list)
    for source in read_rows(curated / "sources.jsonl"):
        if source["source_id"] in page_index:
            source_by_book[source["book_id"]].append(source)
    if sum(map(len, source_by_book.values())) != len(page_index):
        raise ValueError("사건 쪽의 원본 메타데이터 수량이 다릅니다.")

    views = {}
    stats = defaultdict(Counter)
    for book, sources in sorted(source_by_book.items()):
        source_hash = sources[0]["file_sha256"]
        cached = layout / f"{book}-{source_hash[:16]}.txt"
        meta = json.loads(cached.with_suffix(".json").read_bytes())
        layout_bytes = cached.read_bytes()
        if (
            meta["identity"]["pdf_sha256"] != source_hash
            or meta["identity"]["extractor"] != manifest["identity"]["extractor"]
            or meta["text_sha256"] != sha256(layout_bytes).hexdigest()
        ):
            raise ValueError("원본 줄 위치의 레이아웃 캐시 지문이 다릅니다.")
        raw_pages = layout_bytes.decode().split("\f")
        if not raw_pages[-1].strip():
            raw_pages.pop()
        records = defaultdict(list)
        for record in read_rows(curated / "records" / f"{book}.jsonl"):
            records[record["source_id"]].append(record)
        for source in sources:
            sid = source["source_id"]
            _, role, page = page_index[sid]
            text = excerpt(
                raw_pages[source["page"] - 1],
                source,
                records[sid],
                rows_by_source[sid],
                rules_by_source[sid],
                len(raw_pages),
            )
            original_tokens = _count_tokens(page["text"])
            stats[role]["pages"] += 1
            stats[role]["full_page_tokens"] += original_tokens
            if not text:
                stats[role]["unverified_layout"] += 1
            elif _count_tokens(text) >= original_tokens:
                stats[role]["no_token_reduction"] += 1
            else:
                views[location(page)] = {**page, "text": text}
                stats[role]["rewritten_pages"] += 1
            stats[role]["excerpt_tokens"] += _count_tokens(
                views.get(location(page), page)["text"]
            )
    return views, stats, fact_summary


def render_order(ranked, pages, views):
    linked = {location(page) for page in pages}
    return [
        (views.get(location(page), page) if location(page) in linked else page, score)
        for page, score in ranked
    ]


def role_coverage(reference, base, excerpt_context, linked):
    roles = {location(page): role for role, page in zip(ROLES, linked, strict=True)}
    output = defaultdict(Counter)
    for requirement in reference["evidence_requirements"]:
        keys = {(row["book_id"], row["page"]) for row in requirement["primary"]}
        if len(keys) != 1 or (role := roles.get(next(iter(keys)))) is None:
            continue
        evidence = {"evidence_requirements": [requirement]}
        output[role]["facts"] += 1
        for arm, context in zip(ARMS, (base, excerpt_context), strict=True):
            output[role][arm] += fact_coverage(evidence, context)["required_facts_hit"]
    return dict(output)


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        split = "overlap" if row["blind_related"] else "disjoint"
        groups["all"].append(row)
        groups[split].append(row)
        if row["hg_context"]:
            groups[f"{split}:hg"].append(row)
            groups[f"{split}:hg:{row['identifier_mode']}"].append(row)
    output = {}
    for name, items in groups.items():
        roles = defaultdict(Counter)
        for row in items:
            for role, result in row["role_coverage"].items():
                roles[role].update(result)
        output[name] = {
            "questions": len(items),
            "hg_contexts": sum(r["hg_context"] for r in items),
            "changed_contexts": sum(r["changed_context"] for r in items),
            "archived_control_differences": sum(
                r["archived_control_differs"] for r in items
            ),
            "lost_all_facts": sum(
                r["arms"][ARMS[0]]["all_facts"] > r["arms"][ARMS[1]]["all_facts"]
                for r in items
            ),
            "gained_all_facts": sum(
                r["arms"][ARMS[0]]["all_facts"] < r["arms"][ARMS[1]]["all_facts"]
                for r in items
            ),
            "role_coverage": roles,
            "arms": {
                arm: {key: fmean(r["arms"][arm][key] for r in items) for key in METRICS}
                for arm in ARMS
            },
        }
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, default=CURATED)
    parser.add_argument("--layout", type=Path, default=LAYOUT)
    parser.add_argument("--case-index", type=Path, default=CASE_INDEX)
    parser.add_argument("--facts", type=Path, default=FACTS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise SystemExit("기준선과 같은 CUDA FP16 환경이 필요합니다.")
    started = perf_counter()
    questions, inputs = input_files()
    archive = json.loads((ARCHIVE / "config.json").read_bytes())
    previous = json.loads((PREVIOUS / "config.json").read_bytes())
    manifest_bytes = (args.curated / "manifest.json").read_bytes()
    snapshot_sha = sha256(manifest_bytes).hexdigest()
    if (
        inputs != previous["inputs"]
        or fingerprint(CACHE_PATH) != previous["pages"]
        or previous["kure"] != archive["kure"]
        or previous["kure"]["gpu"] != torch.cuda.get_device_name()
        or snapshot_sha != previous["case_index"]["curation_manifest_sha256"]
        or digest(args.case_index / "case_sources.jsonl")
        != previous["case_index"]["case_sources_sha256"]
    ):
        raise ValueError("간접 조회 벤치와 현재 입력이나 실행 환경이 다릅니다.")
    pages = json.loads(CACHE_PATH.read_bytes())
    cases = load_cases(args.case_index, pages, snapshot_sha)
    profiles = ProfileIndex(load_profiles(args.curated, cases))
    views, render_stats, fact_summary = prepare_views(
        args.curated,
        args.layout,
        args.facts,
        cases,
        json.loads(manifest_bytes),
        snapshot_sha,
    )
    if (
        fact_summary["case_sources_sha256"]
        != previous["case_index"]["case_sources_sha256"]
    ):
        raise ValueError("역할별 필드가 다른 사건 인덱스에서 생성됐습니다.")
    selected = {}
    for q in questions:
        text = q["question"]
        direct = matched_case(text, cases)
        case = direct or profiles.candidate(text)[0]
        if case is not None:
            selected[q["id"]] = case, bool(direct)
    active = [q for q in questions if q["id"] in selected]

    args.output.mkdir(parents=True, exist_ok=False)

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with (args.output / "run.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    log(
        f"HG 문맥 비교 · 조회 후보 {len(active):,}문항 · 원본보다 짧은 쪽 {len(views):,}개"
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
            "previous_results": fingerprint(PREVIOUS / "questions.jsonl"),
            "curation_manifest_sha256": snapshot_sha,
            "role_facts": fact_summary,
            "condition_note_pattern": CONDITION_NOTE.pattern,
            "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "generation_calls": 0,
        },
    )
    index = PatternIndex(pages)
    if json.loads(json.dumps(index.config)) != archive["retrieval"]:
        raise ValueError("보관된 제목 보너스 설정과 다릅니다.")
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
            case, direct = selected[q["id"]]
            linked = cases[case]
            if direct or linked_pages(order, linked):
                ranked = augment(order, linked)
                packed[q["id"]] = (
                    ranked,
                    packed_context(ranked),
                    packed_context(render_order(ranked, linked, views)),
                )
        log(
            f"공통 순위로 두 문맥 준비 · {min(start + BATCH_SIZE, len(active)):,}/{len(active):,}문항"
        )

    references = load_references(DATA / "references.json")
    previous_rows = {row["id"]: row for row in read_rows(PREVIOUS / "questions.jsonl")}
    expected_ids = {q["id"] for q in questions}
    if expected_ids != set(previous_rows) or expected_ids != set(references):
        raise ValueError("질문·참고 파일·보관 결과의 ID가 다릅니다.")
    if set(packed) != {
        qid
        for qid, row in previous_rows.items()
        if row["route"] in {"explicit_hg", "triggered"}
    }:
        raise ValueError("간접 조회 벤치의 문맥 적용 대상과 다릅니다.")
    rows = []
    for q in questions:
        qid = q["id"]
        ref, old = references[qid], previous_rows[qid]
        archived = old["arms"]["indirect_lookup"]
        base = variant = archived
        by_role = {}
        changed = False
        if qid in packed:
            ranked, full_context, short_context = packed[qid]
            base = score(q, ranked, full_context, ref)
            variant = score(q, ranked, short_context, ref)
            by_role = role_coverage(
                ref, full_context, short_context, cases[selected[qid][0]]
            )
            changed = full_context.context_hash != short_context.context_hash
        rows.append(
            {
                "id": qid,
                "blind_related": old["blind_related"],
                "question_type": ref["question_type"],
                "identifier_mode": ref["identifier_mode"],
                "hg_context": qid in packed,
                "changed_context": changed,
                "archived_control_differs": base != archived,
                "role_coverage": by_role,
                "arms": dict(zip(ARMS, (base, variant), strict=True)),
            }
        )
    write_rows(args.output / "questions.jsonl", rows)
    write_json(
        args.output / "summary.json",
        {"render": render_stats, "cohorts": summarize(rows)},
    )
    log(f"6,500문항 평가 완료 · 경과 {perf_counter() - started:.1f}초 · 외부 API 0회")


if __name__ == "__main__":
    main()
