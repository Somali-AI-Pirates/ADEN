"""Aggregate retrieval-stage evidence and case-link metrics without raw text."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from statistics import fmean
from typing import Any

from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.context_evidence import _normalize, _source_key

IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{0,6}(?:-[A-Z0-9]{1,8}){1,4}(?![A-Za-z0-9])"
)


def _location(page: Mapping[str, Any]) -> tuple[str, int]:
    return _source_key(page)


def _ranked_locations(
    ranked: Sequence[tuple[Mapping[str, Any], float]],
) -> set[tuple[str, int]]:
    return {_location(page) for page, _ in ranked}


def _context_pages(context: Any) -> dict[tuple[str, int], list[str]]:
    pages: dict[tuple[str, int], list[str]] = defaultdict(list)
    for location, text in _context_chunks(context):
        pages[location].append(_normalize(text))
    return pages


def _context_chunks(context: Any) -> list[tuple[tuple[str, int], str]]:
    chunks = getattr(context, "final_chunks", context)
    output = []
    for item in chunks:
        chunk = (
            item.get("chunk", item)
            if isinstance(item, Mapping)
            else getattr(item, "chunk", item)
        )
        page = (
            chunk
            if isinstance(chunk, Mapping)
            else {
                "book_id": getattr(chunk, "book_id", None),
                "file_name": getattr(chunk, "file_name", None),
                "page": getattr(chunk, "page", None),
                "text": getattr(chunk, "text", None),
            }
        )
        text = page.get("text")
        if not isinstance(text, str):
            raise ValueError("문맥 조각에 원문 텍스트가 없습니다.")
        output.append((_location(page), text))
    return output


def source_anchors_present(normalized_page: str, anchors: Sequence[str]) -> bool:
    """Verify source-line text and its multiplicity inside one restored page."""
    return bool(anchors) and all(
        normalized_page.count(anchor) >= count
        for anchor, count in Counter(anchors).items()
    )


def _citations(reference: Mapping[str, Any]) -> list[list[tuple[str, int, str]]]:
    """Return OR alternatives, one group at a time, in normalized form."""
    groups = reference.get("evidence_groups", ())
    if not isinstance(groups, Sequence) or isinstance(groups, (str, bytes)):
        return []
    output = []
    for group in groups:
        if not isinstance(group, Sequence) or isinstance(group, (str, bytes)):
            continue
        entries = []
        for citation in group:
            if not isinstance(citation, Mapping):
                continue
            quote = citation.get("quote")
            if not isinstance(quote, str) or not _normalize(quote):
                continue
            entries.append((*_location(citation), _normalize(quote)))
        if entries:
            output.append(entries)
    return output


def _audit_quotes(
    audit: Mapping[str, Any] | None, question_id: str
) -> tuple[set[tuple[str, int, str]], dict[tuple[str, int, str], tuple[str, ...]]]:
    if audit is None:
        return set(), {}
    strict_by_id = audit.get("matchable_quotes_by_id", {})
    anchored_by_id = audit.get("reanchored_quotes_by_id", {})
    strict = {
        (str(row[0]), int(row[1]), _normalize(str(row[2])))
        for row in strict_by_id.get(question_id, ())
        if isinstance(row, Sequence)
        and not isinstance(row, (str, bytes))
        and len(row) >= 3
    }
    anchored = {}
    for row in anchored_by_id.get(question_id, ()):
        if (
            not isinstance(row, Sequence)
            or isinstance(row, (str, bytes))
            or len(row) < 4
        ):
            continue
        key = (str(row[0]), int(row[1]), _normalize(str(row[2])))
        anchors = tuple(
            _normalize(str(anchor)) for anchor in row[3] if _normalize(str(anchor))
        )
        if anchors:
            anchored[key] = anchors
    return strict, anchored


def _citation_coverage(
    citation: tuple[str, int, str],
    page_texts: Mapping[tuple[str, int], Sequence[str]],
    reanchored: Mapping[tuple[str, int, str], Sequence[str]],
) -> tuple[bool, bool]:
    book, page, quote = citation
    texts = page_texts.get((book, page), ())
    strict = any(quote in text for text in texts)
    if strict:
        return True, True
    anchors = reanchored.get(citation, ())
    # Keep all anchors together in one page chunk. Never stitch chunks or pages.
    line_reanchored = any(source_anchors_present(text, anchors) for text in texts)
    return line_reanchored, False


def _evidence_group_metrics(
    reference: Mapping[str, Any],
    page_texts: Mapping[tuple[str, int], Sequence[str]],
    *,
    strict_quotes: set[tuple[str, int, str]] | None,
    reanchored_quotes: Mapping[tuple[str, int, str], Sequence[str]],
) -> dict[str, int | float | None]:
    groups = _citations(reference)
    found = strict_found = reanchored_only_found = expected = unscorable = 0
    strict_instances_found = anchored_instances_found = instances_expected = 0

    for group in groups:
        eligible = [
            cite
            for cite in group
            if strict_quotes is None
            or cite in strict_quotes
            or cite in reanchored_quotes
        ]
        if not eligible:
            unscorable += 1
            continue
        expected += 1
        instances_expected += len(eligible)
        group_strict = group_anchored = False
        for citation in eligible:
            calibrated, strict = _citation_coverage(
                citation, page_texts, reanchored_quotes
            )
            group_strict |= strict
            group_anchored |= calibrated
            strict_instances_found += int(strict)
            anchored_instances_found += int(calibrated and not strict)
        strict_found += int(group_strict)
        found += int(group_anchored)
        reanchored_only_found += int(group_anchored and not group_strict)

    return {
        "evidence_groups_expected": expected,
        "evidence_groups_found": found,
        "strict_quote_groups_found": strict_found,
        "line_reanchored_only_groups_found": reanchored_only_found,
        "evidence_groups_unscorable": unscorable,
        "evidence_group_recall": found / expected if expected else None,
        "strict_quote_group_recall": strict_found / expected if expected else None,
        "all_scoreable_evidence_groups": (
            int(found == expected) if expected > 0 else None
        ),
        "all_strict_quote_groups": (
            int(strict_found == expected) if expected > 0 else None
        ),
        "quote_instances_expected": instances_expected,
        "strict_quote_instances_found": strict_instances_found,
        "line_reanchored_quote_instances_found": anchored_instances_found,
    }


def _case_ids(value: Any) -> set[str]:
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (Sequence, set, frozenset)) and not isinstance(
        value, (str, bytes)
    ):
        values = value
    else:
        return set()
    return {str(case) for case in values if str(case).startswith("HG")}


def _page_case_index(
    page_cases: Mapping[tuple[str, int], Any],
) -> dict[tuple[str, int], set[str]]:
    return {key: _case_ids(value) for key, value in page_cases.items()}


def _gold_pages(reference: Mapping[str, Any]) -> set[tuple[str, int]]:
    return {(book, page) for group in _citations(reference) for book, page, _ in group}


def _gold_cases(
    reference: Mapping[str, Any],
    cases_by_page: Mapping[tuple[str, int], set[str]],
) -> set[str]:
    return set().union(
        *(cases_by_page.get(page, set()) for page in _gold_pages(reference))
    )


def _known_cases(
    pages: set[tuple[str, int]],
    cases_by_page: Mapping[tuple[str, int], set[str]],
) -> set[str]:
    return set().union(*(cases_by_page.get(page, set()) for page in pages))


def _answerable(reference: Mapping[str, Any]) -> bool:
    action = reference.get("expected_action")
    if isinstance(action, str):
        return action.strip().casefold() == "answer"
    labels = reference.get("accepted_action_labels")
    return isinstance(labels, Sequence) and "answer" in labels


def _identifier_count(question: str) -> str:
    count = len(set(IDENTIFIER.findall(question)))
    return "2+" if count >= 2 else str(count)


def _family(reference: Mapping[str, Any]) -> str | None:
    for key in (
        "source_family",
        "question_type",
        "generation_method",
        "evaluation_role",
    ):
        value = reference.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _stage_metrics(
    reference: Mapping[str, Any],
    ranked: Sequence[tuple[Mapping[str, Any], float]],
    *,
    top_k: int,
    cases_by_page: Mapping[tuple[str, int], set[str]],
    strict_quotes: set[tuple[str, int, str]] | None,
    reanchored_quotes: Mapping[tuple[str, int, str], Sequence[str]],
    case_evaluable: bool,
) -> dict[str, int | float | None]:
    selected = ranked[:top_k]
    locations = _ranked_locations(selected)
    gold_pages = _gold_pages(reference)
    gold_documents = {book for book, _ in gold_pages}
    citation_groups = _citations(reference)
    page_group_hits = sum(
        any((book, page) in locations for book, page, _ in group)
        for group in citation_groups
    )
    document_group_hits = sum(
        any(book in {found_book for found_book, _ in locations} for book, _, _ in group)
        for group in citation_groups
    )
    retrieved_cases = _known_cases(locations, cases_by_page)
    expected_cases = _gold_cases(reference, cases_by_page)
    evidence = _evidence_group_metrics(
        reference,
        {
            location: [_normalize(page["text"])]
            for page, _ in selected
            for location in [_location(page)]
        },
        strict_quotes=strict_quotes,
        reanchored_quotes=reanchored_quotes,
    )
    personal = (
        fact_coverage(reference, [page for page, _ in selected])
        if reference.get("evidence_requirements")
        else None
    )
    correct_cases = retrieved_cases & expected_cases
    return {
        "gold_pages": len(gold_pages),
        "gold_pages_found": len(gold_pages & locations),
        "page_recall": len(gold_pages & locations) / len(gold_pages)
        if gold_pages
        else None,
        "evidence_page_groups_expected": len(citation_groups),
        "evidence_page_groups_found": page_group_hits,
        "evidence_page_group_recall": (
            page_group_hits / len(citation_groups) if citation_groups else None
        ),
        "gold_documents": len(gold_documents),
        "gold_documents_found": len(gold_documents & {book for book, _ in locations}),
        "document_recall": (
            len(gold_documents & {book for book, _ in locations}) / len(gold_documents)
            if gold_documents
            else None
        ),
        "evidence_document_groups_found": document_group_hits,
        "evidence_document_group_recall": (
            document_group_hits / len(citation_groups) if citation_groups else None
        ),
        "gold_hg_cases": len(expected_cases),
        "gold_hg_cases_found": len(correct_cases),
        "hg_case_recall": len(correct_cases) / len(expected_cases)
        if expected_cases
        else None,
        "unexpected_hg_cases": len(retrieved_cases - expected_cases)
        if case_evaluable
        else None,
        "missing_hg_cases": len(expected_cases - retrieved_cases)
        if case_evaluable
        else None,
        **evidence,
        "personal_required_facts": personal["required_facts"] if personal else None,
        "personal_required_facts_found": personal["required_facts_hit"]
        if personal
        else None,
        "personal_all_facts": personal["all_facts"] if personal else None,
    }


def _context_metrics(
    reference: Mapping[str, Any],
    context: Any,
    *,
    cases_by_page: Mapping[tuple[str, int], set[str]],
    strict_quotes: set[tuple[str, int, str]] | None,
    reanchored_quotes: Mapping[tuple[str, int, str], Sequence[str]],
    case_evaluable: bool,
) -> dict[str, int | float | None]:
    page_texts = _context_pages(context)
    locations = set(page_texts)
    gold_pages = _gold_pages(reference)
    gold_documents = {book for book, _ in gold_pages}
    found_documents = gold_documents & {book for book, _ in locations}
    citation_groups = _citations(reference)
    page_group_hits = sum(
        any((book, page) in locations for book, page, _ in group)
        for group in citation_groups
    )
    document_group_hits = sum(
        any(book in {found_book for found_book, _ in locations} for book, _, _ in group)
        for group in citation_groups
    )
    retrieved_cases = _known_cases(locations, cases_by_page)
    expected_cases = _gold_cases(reference, cases_by_page)
    personal = (
        fact_coverage(reference, context)
        if reference.get("evidence_requirements")
        else None
    )
    return {
        "context_pages": len(locations),
        "gold_pages": len(gold_pages),
        "gold_pages_found": len(gold_pages & locations),
        "page_recall": len(gold_pages & locations) / len(gold_pages)
        if gold_pages
        else None,
        "evidence_page_groups_expected": len(citation_groups),
        "evidence_page_groups_found": page_group_hits,
        "evidence_page_group_recall": (
            page_group_hits / len(citation_groups) if citation_groups else None
        ),
        "gold_documents": len(gold_documents),
        "gold_documents_found": len(found_documents),
        "document_recall": len(found_documents) / len(gold_documents)
        if gold_documents
        else None,
        "evidence_document_groups_found": document_group_hits,
        "evidence_document_group_recall": (
            document_group_hits / len(citation_groups) if citation_groups else None
        ),
        "gold_hg_cases": len(expected_cases),
        "gold_hg_cases_found": len(expected_cases & retrieved_cases),
        "hg_case_recall": (
            len(expected_cases & retrieved_cases) / len(expected_cases)
            if expected_cases
            else None
        ),
        "unexpected_hg_cases": len(retrieved_cases - expected_cases)
        if case_evaluable
        else None,
        "missing_hg_cases": len(expected_cases - retrieved_cases)
        if case_evaluable
        else None,
        **_evidence_group_metrics(
            reference,
            page_texts,
            strict_quotes=strict_quotes,
            reanchored_quotes=reanchored_quotes,
        ),
        "personal_required_facts": personal["required_facts"] if personal else None,
        "personal_required_facts_found": personal["required_facts_hit"]
        if personal
        else None,
        "personal_all_facts": personal["all_facts"] if personal else None,
    }


def _average(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if isinstance(row.get(key), (int, float))]
    return fmean(values) if values else None


def _summarize_records(
    records: Sequence[Mapping[str, Any]], arm_names: Sequence[str]
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        groups["all"].append(record)
        for key in (
            "split",
            "identifier_count",
            "ambiguity",
            "peer_variant",
            "family",
            "action",
        ):
            value = record.get(key)
            if value is not None:
                groups[f"{key}:{value}"].append(record)
        if record.get("split") == "peer_multi_doc":
            groups[
                f"peer_multi_doc_identifier_count:{record['identifier_count']}"
            ].append(record)

    summary = {}
    stage_keys = (
        "document_recall",
        "page_recall",
        "evidence_page_group_recall",
        "evidence_document_group_recall",
        "hg_case_recall",
        "evidence_group_recall",
        "strict_quote_group_recall",
        "all_scoreable_evidence_groups",
        "all_strict_quote_groups",
        "personal_all_facts",
        "unexpected_hg_cases",
        "missing_hg_cases",
    )
    for group, rows in groups.items():
        group_summary: dict[str, Any] = {"questions": len(rows)}
        for arm in arm_names:
            arm_rows = [record["arms"][arm] for record in rows]
            arm_summary = {}
            for stage in ("candidate20", "expanded8", "context"):
                stage_rows = [row[stage] for row in arm_rows if row.get(stage)]
                arm_summary[stage] = {
                    **{key: _average(stage_rows, key) for key in stage_keys},
                    "evidence_groups_expected": sum(
                        row.get("evidence_groups_expected", 0) for row in stage_rows
                    ),
                    "evidence_groups_unscorable": sum(
                        row.get("evidence_groups_unscorable", 0) for row in stage_rows
                    ),
                    "evidence_groups_found": sum(
                        row.get("evidence_groups_found", 0) for row in stage_rows
                    ),
                    "scored_questions": len(stage_rows),
                    "personal_scored_questions": sum(
                        row.get("personal_all_facts") is not None for row in stage_rows
                    ),
                    "personal_all_facts_count": sum(
                        row.get("personal_all_facts", 0) or 0 for row in stage_rows
                    ),
                }
            arm_summary["added_cases"] = {
                "count": sum(row["added_cases_count"] for row in arm_rows),
                "correct": sum(row["added_cases_correct"] or 0 for row in arm_rows),
                "extra": sum(row["added_cases_extra"] or 0 for row in arm_rows),
                "selected_on_evaluable_questions": sum(
                    row["added_cases_count"]
                    for row in arm_rows
                    if row["added_cases_evaluable"]
                ),
                "expected_on_evaluable_questions": sum(
                    row["added_cases_expected_count"] or 0 for row in arm_rows
                ),
                "evaluable_questions": sum(
                    row["added_cases_evaluable"] for row in arm_rows
                ),
            }
            added_summary = arm_summary["added_cases"]
            added_summary["precision"] = (
                added_summary["correct"]
                / added_summary["selected_on_evaluable_questions"]
                if added_summary["selected_on_evaluable_questions"]
                else None
            )
            added_summary["recall"] = (
                added_summary["correct"]
                / added_summary["expected_on_evaluable_questions"]
                if added_summary["expected_on_evaluable_questions"]
                else None
            )
            group_summary[arm] = arm_summary

        if len(arm_names) > 1:
            baseline = arm_names[0]
            for arm in arm_names[1:]:
                paired = [
                    (row["arms"][baseline], row["arms"][arm])
                    for row in rows
                    if row["arms"][baseline].get("context")
                    and row["arms"][arm].get("context")
                ]
                complete_metric = "personal_all_facts"
                evaluable = [
                    (old["context"], new["context"])
                    for old, new in paired
                    if old["context"].get(complete_metric) is not None
                    and new["context"].get(complete_metric) is not None
                ]
                evidence_pairs = [
                    (old["context"], new["context"])
                    for old, new in paired
                    if old["context"].get("all_scoreable_evidence_groups") is not None
                    and new["context"].get("all_scoreable_evidence_groups") is not None
                ]
                strict_quote_pairs = [
                    (old["context"], new["context"])
                    for old, new in paired
                    if old["context"].get("all_strict_quote_groups") is not None
                    and new["context"].get("all_strict_quote_groups") is not None
                ]
                group_summary[f"paired:{arm}_vs_{baseline}"] = {
                    "questions": len(paired),
                    "personal_all_facts_questions": len(evaluable),
                    "personal_all_facts_gained": sum(
                        not old[complete_metric] and new[complete_metric]
                        for old, new in evaluable
                    ),
                    "personal_all_facts_lost": sum(
                        old[complete_metric] and not new[complete_metric]
                        for old, new in evaluable
                    ),
                    "all_evidence_groups_questions": len(evidence_pairs),
                    "all_evidence_groups_gained": sum(
                        not old["all_scoreable_evidence_groups"]
                        and new["all_scoreable_evidence_groups"]
                        for old, new in evidence_pairs
                    ),
                    "all_evidence_groups_lost": sum(
                        old["all_scoreable_evidence_groups"]
                        and not new["all_scoreable_evidence_groups"]
                        for old, new in evidence_pairs
                    ),
                    "strict_quote_questions": len(strict_quote_pairs),
                    "all_strict_quote_groups_gained": sum(
                        not old["all_strict_quote_groups"]
                        and new["all_strict_quote_groups"]
                        for old, new in strict_quote_pairs
                    ),
                    "all_strict_quote_groups_lost": sum(
                        old["all_strict_quote_groups"]
                        and not new["all_strict_quote_groups"]
                        for old, new in strict_quote_pairs
                    ),
                }
        summary[group] = group_summary
    return summary


def score_run(
    questions: Sequence[Mapping[str, Any]],
    retrievals: Mapping[tuple[str, str], tuple[Any, Any, Any, Any, str]],
    references: Mapping[str, Mapping[str, Any]],
    *,
    page_cases: Mapping[tuple[str, int], Any],
    peer_ambiguous: set[str] | frozenset[str] = frozenset(),
    quote_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score flat ``(question_id, arm)`` retrieval tuples.

    Each retrieval tuple is ``(pool, expanded, context, added_cases, route)``.
    The first encountered arm is the paired comparison baseline. Audit-proven
    exact citations are scored strictly; verified re-anchor lines provide a
    separate same-page auxiliary match. The output stores no question or quote
    text and never stores case IDs selected by the lookup.
    """
    question_ids = [str(question["id"]) for question in questions]
    if len(set(question_ids)) != len(question_ids):
        raise ValueError("평가 질문 ID가 중복되었습니다.")
    if set(question_ids) != set(references):
        raise ValueError("질문과 참고 자료 ID가 일치하지 않습니다.")
    requested = set(question_ids)
    retrieval_ids = {str(key[0]) for key in retrievals}
    if retrieval_ids != requested:
        raise ValueError("질문과 검색 결과 ID가 일치하지 않습니다.")
    arm_names = list(dict.fromkeys(str(key[1]) for key in retrievals))
    cases_by_page = _page_case_index(page_cases)
    audited_ids = set()
    if quote_audit is not None:
        for key in (
            "matchable_quotes_by_id",
            "reanchored_quotes_by_id",
            "unmatchable_quotes_by_id",
        ):
            rows = quote_audit.get(key, {})
            if isinstance(rows, Mapping):
                audited_ids.update(str(qid) for qid in rows)
    records = []

    for question in questions:
        qid = str(question["id"])
        reference = references[qid]
        strict_quotes, reanchored_quotes = _audit_quotes(quote_audit, qid)
        # No audit means all labels are evaluated literally. An audit limits
        # quote completeness to citations that have a verified source anchor.
        strict_for_scoring = strict_quotes if qid in audited_ids else None
        expected_cases = _gold_cases(reference, cases_by_page)
        peer = str(question.get("split", "")).startswith("peer_")
        ambiguous = qid in peer_ambiguous
        case_evaluable = (
            _answerable(reference) and bool(expected_cases) and not ambiguous
        )
        family = _family(reference)
        split = str(question.get("split", ""))
        if not peer:
            peer_variant = None
        elif split == "peer_false_premise":
            peer_variant = "false_premise"
        elif qid.startswith("SYF"):
            peer_variant = "synthetic_false"
        else:
            peer_variant = split[5:]
        record: dict[str, Any] = {
            "id": qid,
            "split": question.get("split"),
            "identifier_count": _identifier_count(str(question.get("question", ""))),
            "ambiguity": ("ambiguous" if ambiguous else "unambiguous")
            if peer
            else None,
            "peer_variant": peer_variant,
            "family": family,
            "action": reference.get("expected_action"),
            "answerable_gold_hg_case_scope": int(case_evaluable),
            "arms": {},
        }
        for arm_name in arm_names:
            key = (qid, arm_name)
            if key not in retrievals:
                raise ValueError("질문마다 검색 방식 구성이 다릅니다.")
            pool, expanded, context, added_cases, route = retrievals[key]
            added = _case_ids(added_cases)
            record["arms"][arm_name] = {
                "route": route,
                "added_cases_count": len(added),
                "added_cases_expected_count": len(expected_cases)
                if case_evaluable
                else None,
                "added_cases_correct": len(added & expected_cases)
                if case_evaluable
                else None,
                "added_cases_extra": len(added - expected_cases)
                if case_evaluable
                else None,
                "added_case_precision": (
                    len(added & expected_cases) / len(added)
                    if case_evaluable and added
                    else None
                ),
                "added_case_recall": (
                    len(added & expected_cases) / len(expected_cases)
                    if case_evaluable and expected_cases
                    else None
                ),
                "added_cases_evaluable": int(case_evaluable),
                "candidate20": _stage_metrics(
                    reference,
                    pool,
                    top_k=20,
                    cases_by_page=cases_by_page,
                    strict_quotes=strict_for_scoring,
                    reanchored_quotes=reanchored_quotes,
                    case_evaluable=case_evaluable,
                ),
                "expanded8": _stage_metrics(
                    reference,
                    expanded,
                    top_k=8,
                    cases_by_page=cases_by_page,
                    strict_quotes=strict_for_scoring,
                    reanchored_quotes=reanchored_quotes,
                    case_evaluable=case_evaluable,
                ),
                "context": _context_metrics(
                    reference,
                    context,
                    cases_by_page=cases_by_page,
                    strict_quotes=strict_for_scoring,
                    reanchored_quotes=reanchored_quotes,
                    case_evaluable=case_evaluable,
                ),
            }
        records.append(record)

    return {
        "arm_names": arm_names,
        "scored_questions": len(records),
        "metric_notes": {
            "quote_groups": "Each evidence_groups row is an OR group; any audited strict quote or verified line-reanchored citation in that group counts as covered.",
            "line_reanchored_quotes": "Auxiliary calibrated metric; every normalized source-line anchor, including repeated lines, must occur in one packed chunk on the cited page. Strict quote coverage remains separate.",
            "page_document_recall": "Unique-page and document recall use the union of citation alternatives. The alternative-aware evidence_page_group_recall and evidence_document_group_recall count a group when any cited page or document in that group is retrieved.",
            "hg_case_associations": "Extra and missing packed HG links are scored only on answerable, non-ambiguous rows with at least one known HG case mapped from cited gold pages. Gold cases are the union of known case links on all cited alternative pages; unknown links and unindexed corpus pages are invisible.",
            "peer_ambiguity": "Rows listed in peer_ambiguous are excluded from the incorrect case-association diagnostic.",
        },
        "summary": _summarize_records(records, arm_names),
        "records": records,
    }
