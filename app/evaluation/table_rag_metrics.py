"""Page, required evidence and SQL checks for saved TableRAG runs."""

import math

from app.evaluation.metrics import score


def _page_id(value):
    book = value.get("book_id")
    page = value.get("page")
    return (book, page) if book and type(page) is int and page > 0 else None


def _pages(chunks):
    if chunks is None:
        return None
    return list(dict.fromkeys(page for chunk in chunks if (page := _page_id(chunk))))


def _page_scores(ranking, groups, *, cutoff):
    names = ("page_recall", "required_evidence_all", "mrr", "ndcg")
    if not groups:
        return {
            name: score(None, "not_applicable", "참고 페이지 없음") for name in names
        }
    if ranking is None:
        return {name: score(None, "unavailable", "실행 결과 없음") for name in names}
    ranking = ranking[:cutoff]
    gold = set().union(*groups)
    ranked = set(ranking)
    hits = [rank for rank, page in enumerate(ranking, 1) if page in gold]
    ideal = sum(
        1 / math.log2(rank + 1) for rank in range(1, min(len(gold), cutoff) + 1)
    )
    return {
        "page_recall": score(
            len(ranked & gold) / len(gold), "scored", "book_id와 page 일치"
        ),
        "required_evidence_all": score(
            float(all(group & ranked for group in groups)),
            "scored",
            "모든 필수 근거 묶음의 허용 페이지 포함",
        ),
        "mrr": score(1 / hits[0] if hits else 0.0, "scored", "첫 참고 페이지의 역순위"),
        "ndcg": score(
            sum(1 / math.log2(rank + 1) for rank in hits) / ideal if ideal else 0.0,
            "scored",
            "참고 페이지의 이진 관련도",
        ),
    }


def _sql_score(results, expected):
    if expected is None:
        return score(None, "not_applicable", "참고 SQL 값 없음")
    if (
        not isinstance(expected, list)
        or not expected
        or any(
            not isinstance(row, dict) or not {"columns", "rows"} <= row.keys()
            for row in expected
        )
    ):
        return score(None, "unavailable", "참고 SQL 값 형식 오류")
    if results is None:
        return score(None, "unavailable", "최종 SQL 기록 없음")
    observed = [
        (result.get("columns"), result.get("rows"))
        for result in results
        if result.get("status") == "ok" and not result.get("preview_truncated")
    ]
    matched = all(
        (result["columns"], result["rows"]) in observed for result in expected
    )
    return score(float(matched), "scored", "최종 SQL의 열·행·값 비교")


def table_rag_metrics(reference, record, *, k):
    groups = [
        {page for source in group if (page := _page_id(source))}
        for group in reference.get("evidence_groups") or []
    ]
    groups = [group for group in groups if group]
    final = record.get("final_context_chunks")
    return {
        **{
            f"candidate_{name}": value
            for name, value in _page_scores(
                _pages(record.get("retrieved_candidates")), groups, cutoff=20
            ).items()
        },
        **{
            f"final_{name}": value
            for name, value in _page_scores(_pages(final), groups, cutoff=k).items()
        },
        "final_sql_expected": _sql_score(
            record.get("included_sql_results"), reference.get("expected_sql_results")
        ),
    }
