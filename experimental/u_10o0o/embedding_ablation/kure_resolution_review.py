"""Review actual returned evidence separately from frozen exact-source metrics."""

from copy import deepcopy

from experimental.u_10o0o.advanced_full_metrics import _normalize
from experimental.u_10o0o.pattern_retrieval.common import (
    ROOT,
    file_sha,
    read_json,
    read_jsonl,
    write_json,
)
from experimental.u_10o0o.pattern_retrieval.established_score import (
    aggregates,
    differences,
    page_key,
    score_item,
)
from experimental.u_10o0o.prepare import CACHE_PATH

from . import kure_resolution as experiment


def source_pages():
    return {page_key(x): x for x in read_json(CACHE_PATH)}


def references(frozen):
    from app.evaluation.dataset import load_references

    return {
        s["suite"]: load_references(ROOT / s["references"])
        for s in frozen["question_sources"]
    }


def citation(pages, book, page, start):
    paragraphs = [
        p for p in pages[book, page]["text"].split("\n\n") if p.startswith(start)
    ]
    if len(paragraphs) != 1:
        raise ValueError(
            f"Expected one source paragraph: {book}:{page}, {len(paragraphs)}"
        )
    return {"book_id": book, "page": page, "quote": paragraphs[0]}


def supports(bundle, pages):
    """A substitute must be explicitly approved AND present in the actual returned context."""
    if not bundle:
        return False
    by_key = {}
    for page in pages:
        by_key.setdefault(page_key(page), []).append(_normalize(page["text"]))
    return all(
        isinstance(e.get("quote"), str)
        and bool(_normalize(e["quote"]))
        and any(_normalize(e["quote"]) in text for text in by_key.get(page_key(e), []))
        for e in bundle
    )


def seed(frozen, output):
    refs = references(frozen)
    pages = source_pages()

    def c(b, p, s):
        return citation(pages, b, p, s)

    rules = {
        "official_development:AS-DEV04": {
            "reason": "SI/VGM 정의 차이와 값이 달라도 오류가 아니라는 설명을 한 페이지가 모두 제공한다.",
            "alternatives": [[c("AS009", 21, "SI의 화물 총중량은")]],
        },
        "official_additional:AS-ADD01": {
            "reason": "같은 서비스 자료의 제목·일반 조건·제외 대상이 질문의 항로·화물·장비 범위를 모두 제공한다.",
            "alternatives": [
                [
                    c("AS007", 1, "부산–LA 일반 건화물 서비스"),
                    c("AS007", 1, "대상 장비는"),
                ]
            ],
        },
        "official_additional:AS-ADD07": {
            "reason": "예약 확정·차량 배차와 반입 허가의 차이 및 반입 회신 확인이 모두 명시되어 있다.",
            "alternatives": [[c("AS011", 2, "예약이 확정되어도")]],
        },
        "semantic_queries:AS-SEM-13": {
            "reason": "같은 LA 자료의 현지 날짜·시차 비보충 규칙이 질문의 모든 요청을 충족한다.",
            "alternatives": [
                [c("AS017", 8, "보관일수는 터미널 현지 날짜로")],
                [c("AS017", 14, "날짜만 기재된 기록도")],
            ],
        },
        "semantic_queries:AS-SEM-40": {
            "reason": "같은 부산–LA 서비스의 냉동 별도 조건과 일반 자료 적용 기간을 함께 확인한다.",
            "alternatives": [
                [
                    c("AS046", 72, "태성물산의 일반 건화물"),
                    *deepcopy(
                        refs["semantic_queries"]["AS-SEM-40"]["evidence_requirements"][
                            1
                        ]["primary"]
                    ),
                ]
            ],
        },
    }
    quote = refs["semantic_queries"]["AS-SEM-10"]["evidence_requirements"][0][
        "primary"
    ][0]["quote"]
    rules["semantic_queries:AS-SEM-10"] = {
        "reason": "동일 VGM 자료집에 반복된 일반 상태 판정 문장만 허용한다. 개별 대상의 상태는 전용하지 않는다.",
        "alternatives": [
            [{"book_id": b, "page": p, "quote": quote}]
            for (b, p), row in pages.items()
            if b == "AS010" and _normalize(quote) in _normalize(row["text"])
        ],
    }
    result = {
        "kind": "pre_run_self_review",
        "independent_review": False,
        "rules": rules,
        "ambiguous": {
            "semantic_queries:AS-SEM-32": "질문에 터미널·계약이 없는데 원본별 주말 산입 조건이 다르다."
        },
        "source_sha256": file_sha(CACHE_PATH),
    }
    validate_review(result, pages)
    write_json(output / "pre-review.json", result)


def validate_review(review, pages):
    if review["source_sha256"] != file_sha(CACHE_PATH):
        raise ValueError("Review uses another source snapshot")
    for rule in review["rules"].values():
        if not rule.get("reason") or not rule.get("alternatives"):
            raise ValueError("A review needs an explicit reason and evidence")
        for bundle in rule["alternatives"]:
            if not supports(bundle, [pages[page_key(e)] for e in bundle]):
                raise ValueError("Reviewed quotation is absent from its source")


def semantic_result(original, context, review):
    qid = original["id"]
    if not original["scored"]:
        return {"status": "not_scored", "complete": None}
    if qid in review["ambiguous"]:
        return {"status": "scope_ambiguous", "complete": None}
    if original["stages"]["final"][original["complete_metric"]]:
        return {"status": "original_pass", "complete": True}
    rule = review["rules"].get(qid)
    if rule:
        for i, bundle in enumerate(rule["alternatives"]):
            if supports(bundle, context["pages"]):
                return {
                    "status": "alternative_supported",
                    "complete": True,
                    "bundle": i,
                }
    return {"status": "not_supported", "complete": False}


def score(run, output):
    frozen, manifest = experiment.check(run, output, complete=True)
    if (output / "original-scores.json").exists():
        raise FileExistsError("Keep original scores immutable")
    for r in manifest["references_sha256"]:
        if file_sha(ROOT / r["path"]) != r["sha256"]:
            raise ValueError("Original references changed")
    refs, pages = references(frozen), source_pages()
    original = {}
    pre_review = read_json(output / "pre-review.json")
    pre = {}
    saved = {}
    for arm in experiment.ARMS:
        ts, cs = (
            read_jsonl(output / arm / f"{n}.jsonl") for n in ("traces", "contexts")
        )
        saved[arm] = (ts, cs)
        original[arm] = [
            score_item(meta, refs[meta["suite"]][meta["question_id"]], t, c, pages)
            for meta, t, c in zip(frozen["items"], ts, cs, strict=True)
        ]
        pre[arm] = [
            dict(id=r["id"], **semantic_result(r, c, pre_review))
            for r, c in zip(original[arm], cs, strict=True)
        ]
    comparisons = {}
    for left, right, field in (("B", "C", "reranked_pool"), ("C", "D", "pool")):
        comparisons[f"{left}_{right}_{field}"] = sum(
            a[field] == b[field]
            for a, b in zip(saved[left][0], saved[right][0], strict=True)
        )
        if comparisons[f"{left}_{right}_{field}"] != 280:
            raise ValueError(
                "A supposedly fixed comparison changed its candidate/scores"
            )
    write_json(
        output / "original-scores.json",
        {
            "rows": original,
            "summary": {a: aggregates(rows) for a, rows in original.items()},
            "pre_review": pre,
            "controlled_comparisons": comparisons,
            "differences": {
                a: differences({"baseline": original["A"], "A": rows})
                for a, rows in original.items()
                if a != "A"
            },
        },
    )
    qs = {q["id"]: q["question"] for q in read_json(output / "questions.json")}
    packet = {}
    for a, rows in original.items():
        for row, t, c in zip(rows, *saved[a], strict=True):
            if not row["scored"] or row["stages"]["final"][row["complete_metric"]]:
                continue
            ref = refs[row["suite"]][row["question_id"]]
            entry = packet.setdefault(
                row["id"],
                {
                    "question": qs[row["id"]],
                    "reference": ref,
                    "contexts": {},
                    "stages": {},
                },
            )
            entry["contexts"][a] = c["pages"]
            entry["stages"][a] = {
                s: v[row["complete_metric"]] for s, v in row["stages"].items()
            }
    write_json(output / "failure-review-packet.json", packet)
    experiment.log(
        f"Scored all arms; {len(packet)} distinct questions need failure review"
    )


def report(run, output):
    experiment.check(run, output, complete=True)
    data = read_json(output / "original-scores.json")
    review = read_json(output / "post-review.json")
    pages = source_pages()
    validate_review(review, pages)
    packet = read_json(output / "failure-review-packet.json")
    if set(packet) != set(review.get("decisions", {})):
        raise ValueError("Every failed question needs an explicit review decision")
    result = {}
    for a, rows in data["rows"].items():
        contexts = read_jsonl(output / a / "contexts.jsonl")
        result[a] = [
            dict(id=r["id"], suite=r["suite"], **semantic_result(r, c, review))
            for r, c in zip(rows, contexts, strict=True)
        ]

    def count(rows, suite=None):
        selected = [
            r
            for r in rows
            if r["complete"] is not None
            and (
                suite is None
                or (
                    r["suite"].startswith("official")
                    if suite == "official"
                    else r["suite"] == suite
                )
            )
        ]
        return f"{sum(bool(r['complete']) for r in selected)}/{len(selected)}"

    timer = read_json(output / "timing.json")
    lines = [
        "# KURE-v2 입력·순위·평가 검수 결과",
        "",
        "공식60·카테고리220을 실행했다. 지정 근거 채점은 258개, 범위 모호 SEM-32는 대체 근거 검수 집계에서 분리한다. 이미 확인한 개발·회귀 세트의 자체 검수 결과이며 독립 검증이나 답변 생성 평가가 아니다.",
        "",
        "[실험 조건](protocol.md) · [과정](process.md) · [검수 판정](audit.md) · [검증](validation.md)",
        "",
        "| 조건 | 지정 근거 전체 /258 | 사전 검수 | 전체 사후 검수 | 공식 검수 | 일상 표현 검수 | p50/p95(ms) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for a, rows in result.items():
        g = data["summary"][a]
        raw = sum(
            g[k]["stages"]["final"]["complete"]
            for k in ("official_all", "categories_positive")
        )
        pre = [
            dict(x, suite=data["rows"][a][i]["suite"])
            for i, x in enumerate(data["pre_review"][a])
        ]
        latency = timer["metrics"][a]["total_ms"]
        lines.append(
            f"| {a} | {raw} | {count(pre)} | {count(rows)} | {count(rows, 'official')} | {count(rows, 'semantic_queries')} | {latency['p50']:.1f}/{latency['p95']:.1f} |"
        )
    lines += [
        "",
        "A=기존64/512·Top3+RRF, B=질문128, C=전체 리랭커 순서, D=리랭커1024, E=검색기별50(합집합최대100). 모든 조건의 최종8쪽·8000토큰과 원문 조회는 같다.",
        "",
        "사후 검수는 같은 승인 근거 묶음을 모든 조건의 실제 반환 문맥과 대조한다. 다른 사건의 값이나 적용 범위가 다른 규칙을 전용하지 않는다. 지정 페이지를 못 찾아도 충분한 대체 원문을 실제로 반환했다면 통과한다. 사전 검수 효과와 사후 추가 출처 효과를 분리했다.",
        "",
        "## 재현",
        "",
        chr(96) * 3 + "bash",
    ]
    for c in ("prepare", "retrieve", "score", "timing", "report"):
        lines.append(
            f".venv/bin/python -m experimental.u_10o0o.embedding_ablation.kure_resolution {c}"
        )
    lines += [
        chr(96) * 3,
        "",
        "report 전에 로컬 post-review.json의 전체 실패 검수를 완료해야 한다. 이전 경로 덮어쓰기는 금지하며 --output으로 새 경로를 지정한다.",
        "",
    ]
    (experiment.RECORDS / "report.md").write_text("\n".join(lines), encoding="utf-8")
    audit = [
        "# 실패 결과의 실제 근거 검수",
        "",
        "독립/사람 검수 없이 원문을 자체 대조했다. 인용문과 질문은 로컬 artifacts에만 보관한다. 같은 문항의 각 조건에 허용 근거를 대칭 적용했다.",
        "",
        "| 문항 | 판정 근거 | A | B | C | D | E |",
        "|---|---|---|---|---|---|---|",
    ]
    byid = {a: {r["id"]: r for r in rows} for a, rows in result.items()}
    for q, reason in review["decisions"].items():
        statuses = [byid[a][q]["status"] for a in experiment.ARMS]
        audit.append("| " + q + " | " + reason + " | " + " | ".join(statuses) + " |")
    (experiment.RECORDS / "audit.md").write_text(
        "\n".join(audit) + "\n", encoding="utf-8"
    )
    write_json(
        experiment.RECORDS / "results.json",
        {
            "original_summary": data["summary"],
            "pre_review": data["pre_review"],
            "post_review": result,
            "timing": timer["metrics"],
            "review_sha256": file_sha(output / "post-review.json"),
            "manifest_sha256": file_sha(output / "manifest.json"),
            "comparisons": data["controlled_comparisons"],
            "external_api_calls": 0,
        },
    )
    experiment.log("Report saved with original, pre-reviewed and post-reviewed scores")
