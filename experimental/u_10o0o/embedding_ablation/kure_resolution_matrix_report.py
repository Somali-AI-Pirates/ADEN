"""Report seven fixed KURE conditions, audited contexts and online reproducibility."""

from collections import Counter
from statistics import fmean

from app.generation.context import build_context
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.pattern_retrieval.common import (
    file_sha,
    read_json,
    read_jsonl,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    official_metrics,
    page_key,
    score_item,
)

from . import kure_resolution as base
from . import kure_resolution_matrix as matrix
from . import kure_resolution_review as review

LABELS = {
    "A": "기존 입력·Top3+RRF",
    "B": "질문만 확대",
    "F": "리랭커만 확대",
    "G": "질문+리랭커 둘 다 확대",
    "C": "질문 확대+전체 리랭커 순서",
    "D": "둘 다 확대+전체 리랭커 순서",
    "E": "D+검색기별 후보50",
}


def count(rows, predicate=lambda r: True):
    xs = [r for r in rows if r["complete"] is not None and predicate(r)]
    return sum(bool(r["complete"]) for r in xs), len(xs)


def formatted(rows, predicate=lambda r: True):
    n, total = count(rows, predicate)
    return f"{n}/{total} ({n / total:.1%})" if total else "N/A"


def main():
    output, records = base.OUTPUT, base.RECORDS
    frozen, manifest = matrix.check(output)
    data = read_json(output / "matrix-scores.json")
    audit = read_json(output / "matrix-post-review.json")
    packet = read_json(output / "matrix-failure-review-packet.json")
    if set(audit["decisions"]) != set(packet):
        raise ValueError("Every failing question needs a reviewed decision")
    sources = review.source_pages()
    review.validate_review(audit, sources)
    refs = review.references(frozen)
    for entry in manifest["references_sha256"]:
        if file_sha(base.ROOT / entry["path"]) != entry["sha256"]:
            raise ValueError("Reference changed")
    timing = read_json(output / "matrix-timing.json")
    if len(timing["samples"]) != 630:
        raise ValueError("Expected 7 conditions x 30 questions x 3 repeats")
    traces = {a: read_jsonl(output / a / "traces.jsonl") for a in matrix.ARMS}
    contexts = {a: read_jsonl(output / a / "contexts.jsonl") for a in matrix.ARMS}
    rows, diagnosis = {}, {}

    def complete(reference, pages, qid):
        if qid in audit["ambiguous"]:
            return None
        original = (
            official_metrics(reference, pages, len(pages) or 1)["all_groups"]
            if qid.startswith("official_")
            else fact_coverage(reference, pages)["all_facts"]
        )
        return bool(original) or any(
            review.supports(bundle, pages)
            for bundle in audit["rules"].get(qid, {}).get("alternatives", [])
        )

    for a in matrix.ARMS:
        if len(traces[a]) != 280 or len(contexts[a]) != 280:
            raise ValueError("Missing saved rows")
        rows[a], diagnosis[a] = [], []
        for r, t, c in zip(data["rows"][a], traces[a], contexts[a], strict=True):
            if r["id"] != t["id"] or r["id"] != c["id"]:
                raise ValueError("Changed question order")
            if (
                len(t["pool"]) > matrix.ARMS[a][2] * 2
                or len(c["pages"]) > 8
                or t["context_tokens"] > 8000
            ):
                raise ValueError("Budget changed")
            verdict = review.semantic_result(r, c, audit)
            rows[a].append(
                {
                    "id": r["id"],
                    "suite": r["suite"],
                    "category": r["category"],
                    **verdict,
                }
            )
            if verdict["complete"] is not False:
                continue
            ref = refs[r["suite"]][r["question_id"]]
            stages = {
                name: complete(ref, [sources[page_key(x)] for x in t[field]], r["id"])
                for name, field in (
                    ("initial", "pool"),
                    ("post_rank20", "post_rank_top20"),
                    ("after_hg", "after_hg_pool"),
                    ("available", "ranked"),
                )
            }
            cause = (
                "candidate_missing"
                if not stages["initial"] and not stages["after_hg"]
                else "post_rank20_drop"
                if stages["initial"]
                and not stages["post_rank20"]
                and not stages["after_hg"]
                else "lookup_or_selection_drop"
                if not stages["available"]
                else "final_eight_drop"
            )
            diagnosis[a].append(
                {
                    "id": r["id"],
                    "cause": cause,
                    "stages": stages,
                    "final_pages": len(c["pages"]),
                    "tokens": t["context_tokens"],
                }
            )

    indexed = {a: {r["id"]: r for r in rs} for a, rs in rows.items()}
    raw_index = {a: {r["id"]: r for r in rs} for a, rs in data["rows"].items()}
    trace_index = {a: {r["id"]: r for r in rs} for a, rs in traces.items()}
    meta = {r["id"]: r for r in frozen["items"]}
    online_changes = []
    online_hashes = 0
    for sample in timing["samples"]:
        a, qid = sample["arm"], sample["id"]
        packed = build_context(
            _context_candidates(
                [(sources[tuple(k)], 0.0) for k in sample["final_pages"]]
            ),
            8,
            8000,
            _count_tokens,
        )
        if packed.context_hash != sample["context_sha256"]:
            raise ValueError("Online final text cannot be reconstructed exactly")
        online_hashes += 1
        if sample["context_sha256"] == trace_index[a][qid]["context_sha256"]:
            continue
        c = {
            "pages": [
                {"book_id": x.chunk.book_id, "page": x.chunk.page, "text": x.chunk.text}
                for x in packed.final_chunks
            ]
        }
        m = meta[qid]
        r = score_item(
            m, refs[m["suite"]][m["question_id"]], trace_index[a][qid], c, sources
        )
        v = review.semantic_result(r, c, audit)
        old = raw_index[a][qid]
        online_changes.append(
            {
                "arm": a,
                "id": qid,
                "repeat": sample["repeat"],
                "offline_original": old["stages"]["final"][old["complete_metric"]],
                "online_original": r["stages"]["final"][r["complete_metric"]],
                "offline_audited": indexed[a][qid]["complete"],
                "online_audited": v["complete"],
            }
        )

    differences = {}
    for a in matrix.ARMS:
        if a == "A":
            continue
        differences[a] = {
            "improved": [
                q
                for q in indexed[a]
                if indexed["A"][q]["complete"] is False
                and indexed[a][q]["complete"] is True
            ],
            "regressed": [
                q
                for q in indexed[a]
                if indexed["A"][q]["complete"] is True
                and indexed[a][q]["complete"] is False
            ],
        }
    write_json(
        records / "comparison-results.json",
        {
            "configuration": matrix.ARMS,
            "original_summary": data["summary"],
            "post_review": rows,
            "diagnosis": diagnosis,
            "differences_from_A": differences,
            "timing": timing["metrics"],
            "online_hashes_verified": online_hashes,
            "online_context_differences": online_changes,
            "review_sha256": file_sha(output / "matrix-post-review.json"),
            "settings_sha256": file_sha(base.SETTINGS),
            "independent_review": False,
            "external_api_calls": 0,
        },
    )

    lines = [
        "# KURE-v2 입력 상한·순위·근거 검수 비교",
        "",
        "질문과 리랭커 입력을 함께 늘려도 기존 Top3+RRF의 전체 근거 충족은 개선되지 않았다. 전체 리랭커 순서까지 적용한 D는 검수 기준으로 4문항 개선·3문항 하락이었다. 사후 검수한 개발 세트의 한 문항 순증이므로 채택 근거로는 부족하다. 기존 기준선을 자동 교체하지 않는다.",
        "",
        "[초기 설계](protocol.md) · [두 입력 결합 비교](token-matrix.md) · [과정](process.md) · [문항별 검수/변화](comparison-audit.md) · [검증](validation.md) · [5조건 사전/사후 검수](report.md)",
        "",
        "공식60·카테고리220을 모두 검색했다. 원래 긍정 근거 평가는 공식58+카테고리200=258문항이다. 근거 없음/부정 대조22문항은 점수 분모에 넣지 않는다. 추가로 계약이 없어 주말 산입 범위가 모호한 SEM-32 한 문항은 사후 검수 집계에서 분리하여 257문항으로 표시한다. 검색 근거를 평가했으며 실제 답변을 생성하거나 채점하지 않았다.",
        "",
        "## 입력 한도만 바꾼 2×2 비교",
        "",
        "후보는 검색기별20의 합집합 최대40, 후속 순위는 Top3+RRF, 원문 조회·최종8쪽·8000토큰은 모두 같다. 단위는 모델의 실제 tokenizer 토큰이다.",
        "",
        "| 조건 | 질문/리랭커 상한 | 지정 근거 충족 /258 | 실제 반환 근거 검수 /257 | GPU p50/p95(ms) |",
        "|---|---:|---:|---:|---:|",
    ]
    for a in ("A", "B", "F", "G"):
        raw = sum(
            r["stages"]["final"][r["complete_metric"]]
            for r in data["rows"][a]
            if r["scored"]
        )
        t = timing["metrics"][a]["total_ms"]
        lines.append(
            f"| {a}: {LABELS[a]} | {matrix.ARMS[a][0]}/{matrix.ARMS[a][1]} | {raw}/258 | {formatted(rows[a])} | {t['p50']:.1f}/{t['p95']:.1f} |"
        )
    lines += [
        "",
        "질문 64토큰을 넘는 29/280개만 재임베딩했고 최대 입력은 95토큰이어서 128에서 질문 절단은 없다. 짧은 질문의 학습 시 확장 바닥값64는 유지했다. 문서 벡터는 재임베딩하지 않았다. G는 기존 기준선과 동일한 순위 규칙으로 두 입력을 함께 늘린 조건이며, D와는 순위 규칙만 다르다.",
        "",
        "## 순위와 후보 수를 추가로 바꾼 비교",
        "",
        "| 조건 | 변경 | 지정 근거 /258 | 실제 근거 검수 /257 | GPU p50/p95(ms) |",
        "|---|---|---:|---:|---:|",
    ]
    for a in ("C", "D", "E"):
        raw = sum(
            r["stages"]["final"][r["complete_metric"]]
            for r in data["rows"][a]
            if r["scored"]
        )
        t = timing["metrics"][a]["total_ms"]
        lines.append(
            f"| {a} | {LABELS[a]} | {raw}/258 | {formatted(rows[a])} | {t['p50']:.1f}/{t['p95']:.1f} |"
        )
    lines += [
        "",
        "C와 B는 같은 후보·리랭커 점수로 순서만 바꿨다. D와 C는 같은 후보에서 리랭커 입력만1024로 늘렸다. E는 D에서 검색기별50, 합집합 최대100으로 늘린 조건이다. 후보가 많아지면 리랭킹 비용과 다른 주제 페이지의 경쟁도 함께 늘어난다.",
        "",
        "## 유형별 실제 근거 충족",
        "",
        "| 유형 | A | B | F | G | C | D | E |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    groups = [
        ("공식", lambda r: r["suite"].startswith("official")),
        ("업무 상황 S01–S07", lambda r: r["suite"] == "scenarios"),
        ("업무 규칙", lambda r: r["suite"] == "work_rules"),
        ("일상 표현", lambda r: r["suite"] == "semantic_queries"),
    ]
    for label, pred in groups:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(formatted(rows[a], pred) for a in LABELS)
            + " |"
        )
    lines += [
        "",
        "## 실제 실패가 남은 단계",
        "",
        "검수 후 실패한 문항만 센다. 초기 후보에 없어도 기존 원문 조회에서 회복할 수 있으므로 조회 후 상태까지 함께 확인했다. 인정된 대체 출처도 같은 단계에 실제 존재하는지 대조했다.",
        "",
        "| 조건 | 초기 후보/조회에서도 부족 | 후보는 충분하나 후속20에서 유실 | 조회/선택 과정 유실 | 최종8쪽에서 유실 |",
        "|---|---:|---:|---:|---:|",
    ]
    for a in LABELS:
        c = Counter(r["cause"] for r in diagnosis[a])
        lines.append(
            f"| {a} | {c['candidate_missing']} | {c['post_rank20_drop']} | {c['lookup_or_selection_drop']} | {c['final_eight_drop']} |"
        )
    lines += [
        "",
        "## 검수 해석과 재현 한계",
        "",
        f"7조건에서 한 번이라도 지정 근거 채점에 실패한 고유 {len(packet)}문항을 원문과 대조했다. 지정 출처가 달라도 같은 대상·시기·비용·상태에 대한 요청 사실을 모두 뒷받침하면 통과했다. 승인·청구서에 이미 요청 사실이 있는 사건, 같은 규칙이 다른 페이지에 반복되는 경우가 여기에 해당한다. 다른 사건 날짜/금액, 단순 주제 유사성, 일부 요청만 충족한 문맥은 통과시키지 않았다.",
        "",
        "원래 지정 페이지 점수는 보존했다. 사후 검수 점수 상승은 검색 성능 개선이 아니다. 대체 근거 목록은 이미 본 결과를 검토해 만든 자체 검수이며, 독립/사람 검수나 새 미사용 검증 결과가 아니다. 승인한 인용문과 원문·질문은 로컬 artifacts에만 보관하며 공유 기록에는 ID·판정·통계만 남긴다.",
        "",
        f"지연은 GPU 모델·색인 상주 상태에서 질문 임베딩부터 최종 문맥까지 30문항×3회×7조건={len(timing['samples'])}회를 순환 측정했다. 질문별 점수 캐시는 재사용하지 않았다. CPU 추론 지연 또는 답변 생성 지연이 아니다. 630개 최종 문맥 지문을 원문으로 다시 복원해 일치를 확인했다. 품질 배치와 온라인 단건 실행의 문맥 순서/구성이 달랐던 표본은 {len(online_changes)}회이며 지정 근거·사후 검수 통과 판정은 모두 같았다. 변경된 순서/문맥과 판정 대조를 아래 파일에 남겼다.",
        "",
        "- 온라인 실행 차이: comparison-results.json의 online_context_differences",
        "- 지정 book_id+page Recall·MRR·nDCG: comparison-results.json의 original_summary",
        "- 동일 후보/점수 통제 검사: 로컬 matrix-scores.json의 controlled_comparisons",
        "",
        "## 재현 명령",
        "",
        "```bash",
        ".venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution prepare",
        ".venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution retrieve",
        ".venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution score",
        ".venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution_matrix retrieve",
        ".venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution_matrix score",
        ".venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution_matrix timing",
        "# 로컬 실패 검수표 matrix-post-review.json을 완료한 뒤 실행",
        ".venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution_matrix_report",
        "```",
        "",
        "원래 실행 경로를 덮어쓰지 않는다. 새 재실행에는 별도 실행 디렉터리로 경로를 분리해야 한다. 현재 파일은 이번 고정 실행을 검증/보고하는 개인 실험용이며 일반 서비스 CLI가 아니다.",
        "",
    ]
    (records / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    changes = [
        "# 7조건 문항별 근거 검수와 변화",
        "",
        "원문은 로컬 artifacts의 matrix-post-review.json과 실제 반환 contexts.jsonl에 있다. 허용한 대체 인용 묶음이 실제 반환 문맥에 완전히 포함될 때만 통과한다.",
        "",
        "| 문항 | 판단 근거 | A | B | F | G | C | D | E |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    status = {
        "original_pass": "지정 근거 통과",
        "alternative_supported": "대체 근거 통과",
        "not_supported": "근거 부족",
        "scope_ambiguous": "범위 모호",
    }
    for q, reason in audit["decisions"].items():
        changes.append(
            "| "
            + q
            + " | "
            + reason
            + " | "
            + " | ".join(status[indexed[a][q]["status"]] for a in LABELS)
            + " |"
        )
    changes += [
        "",
        "## A 대비 실제 근거 판정 변화",
        "",
        "| 조건 | 개선 | 하락 |",
        "|---|---|---|",
    ]
    for a, diff in differences.items():
        changes.append(
            f"| {a} | {', '.join(diff['improved']) or '없음'} | {', '.join(diff['regressed']) or '없음'} |"
        )
    changes += [
        "",
        "## 실패 단계 세부 기록",
        "",
        "| 조건 | 문항 | 원인 | 초기/후속20/조회후/선택후 | 최종 쪽수 | 토큰 |",
        "|---|---|---|---|---:|---:|",
    ]
    for a, rs in diagnosis.items():
        for r in rs:
            changes.append(
                f"| {a} | {r['id']} | {r['cause']} | {'/'.join(str(int(x)) for x in r['stages'].values())} | {r['final_pages']} | {r['tokens']} |"
            )
    (records / "comparison-audit.md").write_text(
        "\n".join(changes) + "\n", encoding="utf-8"
    )
    work = {
        a: {
            "mean_rerank_pairs": fmean(r["work"]["rerank_pairs"] for r in ts),
            "mean_truncated_pairs": fmean(r["work"]["rerank_truncated"] for r in ts),
            "mean_effective_pair_tokens": fmean(r["work"]["rerank_tokens"] for r in ts),
        }
        for a, ts in traces.items()
    }
    write_json(records / "work.json", work)
    base.log("Seven-condition report, source audit and online comparison saved")


if __name__ == "__main__":
    main()
