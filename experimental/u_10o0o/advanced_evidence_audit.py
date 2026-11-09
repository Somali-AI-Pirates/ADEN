"""Audit Advanced evidence labels and prepare a private blind review packet."""

from __future__ import annotations

import json
import argparse
import random
import re
import subprocess
from collections import Counter, defaultdict
from functools import cache
from hashlib import sha256
from pathlib import Path
from statistics import fmean, median

from pypdf import PdfReader

from experimental.u_10o0o.advanced_patterns import location, packed_context, write_json
from experimental.u_10o0o.advanced_target_metrics import source_anchors_present
from experimental.u_10o0o.context_evidence import _normalize
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR, PROJECT_ROOT

PEER_DATA = PROJECT_ROOT / "experimental/ysl727727/overnight/data"
PEER_RUN = PROJECT_ROOT / "artifacts/u_10o0o/advanced-peer-suite-20261109/results.json"
PERSONAL_BLIND = (
    Path(__file__).with_name("evaluation") / "advanced_usage_blind" / "local"
)
FULL_COVERAGE = PROJECT_ROOT / "experimental/u_10o0o/evaluation/advanced_full_coverage"
OUTPUT = PROJECT_ROOT / "artifacts/u_10o0o/advanced-evidence-audit-20261109"
SEED = 20261109
ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{0,6}(?:-[A-Z0-9]{1,8}){1,4}(?![A-Za-z0-9])"
)


def read_json(path: Path):
    return json.loads(path.read_bytes())


def read_jsonl(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def citation_keys(reference):
    return {
        (item["book_id"], item["page"], _normalize(item["quote"]))
        for group in reference.get("evidence_groups", ())
        for item in group
        if item.get("quote")
    }


def quote_groups(reference):
    return [
        [{**item, "text": item.get("quote", "")} for item in group if item.get("quote")]
        for group in reference.get("evidence_groups", ())
    ]


def quote_key_rows(keys):
    return [[book_id, page, quote] for book_id, page, quote in sorted(keys)]


def context_quote_keys(ranked, expected):
    context = packed_context(ranked[:8])
    text_by_page = defaultdict(list)
    for result in context.final_chunks:
        chunk = result.chunk
        text_by_page[chunk.book_id, chunk.page].append(_normalize(chunk.text))
    found = {
        key
        for key in expected
        if any(key[2] in text for text in text_by_page.get(key[:2], ()))
    }
    return found, context.token_count


def context_page_text(ranked):
    context = packed_context(ranked)
    text_by_page = defaultdict(list)
    for result in context.final_chunks:
        chunk = result.chunk
        text_by_page[chunk.book_id, chunk.page].append(_normalize(chunk.text))
    return text_by_page


def quote_line_anchors(original_page_text: str, normalized_quote: str):
    normalized_page = _normalize(original_page_text)
    start = normalized_page.find(normalized_quote)
    if start < 0:
        return ()
    end = start + len(normalized_quote)
    lines = [_normalize(line) for line in original_page_text.splitlines()]
    lines = [line for line in lines if line]
    if "".join(lines) != normalized_page:
        return ()
    anchors, offset = [], 0
    for line in lines:
        line_end = offset + len(line)
        if offset < end and line_end > start:
            anchors.append(line)
        offset = line_end
    return tuple(anchors)


def peer_files():
    return {
        "false_premise_adv": (
            PEER_DATA / "false_premise_adv_questions.json",
            PEER_DATA / "false_premise_adv.json",
        ),
        "synth_adv": (
            PEER_DATA / "synth_adv_questions.json",
            PEER_DATA / "synth_adv.json",
        ),
        "synth_multi_adv": (
            PEER_DATA / "synth_multi_adv_questions.json",
            PEER_DATA / "synth_multi_adv.json",
        ),
    }


def load_peer():
    questions, references, splits = {}, {}, {}
    for split, (questions_path, references_path) in peer_files().items():
        question_rows = read_json(questions_path)
        reference_rows = read_json(references_path)
        if {row["id"] for row in question_rows} != {
            row["id"] for row in reference_rows
        }:
            raise ValueError(f"{split} 질문과 참고 ID가 다릅니다.")
        questions.update({row["id"]: row["question"] for row in question_rows})
        references.update({row["id"]: row for row in reference_rows})
        splits.update({row["id"]: split for row in question_rows})
    return questions, references, splits


def load_rankings():
    run = read_json(PEER_RUN)
    if run["config"]["suite"] != "peer":
        raise ValueError("저장된 실행은 동료 평가 세트 실행이 아닙니다.")
    for input_files in run["config"]["inputs"].values():
        for input_data in input_files.values():
            path = PROJECT_ROOT / input_data["path"]
            if sha256(path.read_bytes()).hexdigest() != input_data["sha256"]:
                raise ValueError("실행에 사용한 동료 평가 파일 지문이 바뀌었습니다.")
    results = next(iter(run["results"].values()))["records"]
    return (
        run,
        {row["id"]: row["ranked"] for row in results},
        {row["id"]: row["metrics"] for row in results},
    )


def audit_peer():
    questions, references, splits = load_peer()
    pages = json.loads(CACHE_PATH.read_bytes())
    page_by_key = {location(page): page for page in pages}
    run, rankings, saved_metrics = load_rankings()

    valid_by_id, invalid_by_id, reanchored_by_id, score_by_id = {}, {}, {}, {}
    source_check_by_id = {}
    token_counts = {}
    for qid, reference in references.items():
        expected = citation_keys(reference)
        valid, invalid, reanchored, source_checks = set(), {}, {}, Counter()
        for book_id, page_number, quote in expected:
            page = page_by_key.get((book_id, page_number))
            if page is None:
                invalid[book_id, page_number, quote] = (
                    "page_missing_from_restored_cache"
                )
                source_checks["page_missing_from_restored"] += 1
                continue
            restored_text = _normalize(page["text"])
            if quote in restored_text:
                valid.add((book_id, page_number, quote))
                source_checks["restored_page_exact"] += 1
                continue
            original = pdf_text(book_id, page_number)
            pypdf_text = original["pypdf_plain"]
            poppler_text = original["poppler_plain"]
            pypdf_match = quote in _normalize(pypdf_text)
            poppler_match = quote in _normalize(poppler_text)
            source_checks["pypdf_plain_page_exact"] += pypdf_match
            source_checks["poppler_plain_page_exact"] += poppler_match
            if not (pypdf_match and poppler_match):
                invalid[book_id, page_number, quote] = (
                    "not_exact_in_both_independent_plain_page_extractors"
                )
                continue
            source_checks["original_extractors_both_exact"] += 1
            anchors = quote_line_anchors(pypdf_text, quote)
            if source_anchors_present(restored_text, anchors):
                reanchored[book_id, page_number, quote] = anchors
                source_checks["whole_line_anchors_all_present"] += 1
                source_checks["whole_line_anchor_sets_unique"] += all(
                    restored_text.count(anchor) == 1 for anchor in anchors
                )
            else:
                invalid[book_id, page_number, quote] = (
                    "source_quote_verified_but_whole_line_anchor_not_locatable_or_absent"
                )
                source_checks["whole_line_anchors_missing_or_unlocatable"] += 1

        ranked = []
        for item in rankings[qid]:
            page = page_by_key.get((item["book_id"], item["page"]))
            if page is None:
                raise ValueError("저장된 순위에 없는 원본 페이지가 있습니다.")
            ranked.append((page, 0.0))
        found, token_count = context_quote_keys(ranked, expected)
        text_by_page = context_page_text(ranked[:8])
        reanchored_found = set()
        for key, anchors in reanchored.items():
            if any(
                source_anchors_present(text, anchors)
                for text in text_by_page.get(key[:2], ())
            ):
                reanchored_found.add(key)
        exact_found = found & valid
        valid_found = exact_found | reanchored_found
        auditable = valid | set(reanchored)
        original_fraction = len(found) / len(expected) if expected else None
        strict_fraction = len(exact_found) / len(expected) if expected else None
        line_reanchored_fraction = (
            len(valid_found) / len(expected) if expected else None
        )
        exact_fraction = len(exact_found) / len(valid) if valid else None
        calibrated_fraction = len(valid_found) / len(auditable) if auditable else None
        complete_quoteable = int(bool(auditable) and valid_found == auditable)
        metrics = saved_metrics[qid]
        score_by_id[qid] = {
            "split": splits[qid],
            "original_citations": len(expected),
            "source_verified_citations": len(valid) + len(reanchored),
            "restored_matchable_citations": len(valid),
            "whole_line_reanchored_citations": len(reanchored),
            "restored_unmatchable_citations": len(invalid),
            "original_quote_recall": original_fraction,
            "strict_exact_quote_recall": strict_fraction,
            "line_reanchored_quote_recall": line_reanchored_fraction,
            "strict_quote_recall_on_exact_subset": exact_fraction,
            "matchable_quote_recall": calibrated_fraction,
            "all_matchable_quotes_present": complete_quoteable,
            "saved_quote_recall": metrics["quote_recall@8"],
            "page_recall_at_8": metrics["page_recall@8"],
            "document_recall_at_8": metrics["doc_recall@8"],
            "context_tokens": token_count,
        }
        valid_by_id[qid] = valid
        invalid_by_id[qid] = invalid
        reanchored_by_id[qid] = reanchored
        source_check_by_id[qid] = dict(source_checks)
        token_counts[qid] = token_count

    per_split = defaultdict(list)
    for qid, values in score_by_id.items():
        per_split[values["split"]].append(values)
    summary_splits = {}
    for split, rows in per_split.items():
        scored_rows = [row for row in rows if row["matchable_quote_recall"] is not None]
        original_rows = [
            row for row in rows if row["original_quote_recall"] is not None
        ]
        summary_splits[split] = {
            "questions": len(rows),
            "questions_fully_quoteable": sum(
                row["restored_unmatchable_citations"] == 0 for row in rows
            ),
            "original_quote_recall_macro": fmean(
                row["original_quote_recall"] for row in original_rows
            ),
            "strict_exact_quote_recall_macro": fmean(
                row["strict_exact_quote_recall"] for row in rows
            ),
            "line_reanchored_quote_recall_macro": fmean(
                row["line_reanchored_quote_recall"] for row in rows
            ),
            "matchable_quote_recall_macro": fmean(
                row["matchable_quote_recall"] for row in scored_rows
            ),
            "strict_quote_recall_exact_only_macro": fmean(
                row["strict_quote_recall_on_exact_subset"]
                for row in rows
                if row["strict_quote_recall_on_exact_subset"] is not None
            ),
            "all_matchable_quotes_present": sum(
                row["all_matchable_quotes_present"] for row in rows
            ),
            "documents_recall_at_8": fmean(row["document_recall_at_8"] for row in rows),
            "pages_recall_at_8": fmean(row["page_recall_at_8"] for row in rows),
        }

    all_expected = sum(row["original_citations"] for row in score_by_id.values())
    all_matchable = sum(
        row["restored_matchable_citations"] for row in score_by_id.values()
    )
    all_unmatchable = sum(
        row["restored_unmatchable_citations"] for row in score_by_id.values()
    )
    summary = {
        "input_sha256": {
            "peer_run": sha256(PEER_RUN.read_bytes()).hexdigest(),
            "restored_pages": sha256(CACHE_PATH.read_bytes()).hexdigest(),
        },
        "peer_questions": len(questions),
        "peer_quote_totals": {
            "citations": all_expected,
            "restored_matchable": all_matchable,
            "whole_line_reanchored": sum(
                row["whole_line_reanchored_citations"] for row in score_by_id.values()
            ),
            "restored_unmatchable": all_unmatchable,
            "questions_with_unmatchable_citations": sum(
                row["restored_unmatchable_citations"] > 0
                for row in score_by_id.values()
            ),
        },
        "source_validation_for_restored_mismatches": {
            "questions_with_mismatches": sum(
                bool(reanchored_by_id[qid] or invalid_by_id[qid]) for qid in references
            ),
            "mismatch_citations": sum(
                len(reanchored_by_id[qid]) + len(invalid_by_id[qid])
                for qid in references
            ),
            "new_unscorable_citations": sum(
                len(invalid_by_id[qid]) for qid in references
            ),
            **{
                name: sum(counts.get(name, 0) for counts in source_check_by_id.values())
                for name in (
                    "pypdf_plain_page_exact",
                    "poppler_plain_page_exact",
                    "original_extractors_both_exact",
                    "whole_line_anchors_all_present",
                    "whole_line_anchor_sets_unique",
                    "whole_line_anchors_missing_or_unlocatable",
                )
            },
        },
        "subsets": summary_splits,
        "peer_ambiguous_2doc_question_count": len(
            read_json(PEER_DATA / "synth_adv_ambiguous.json")
        ),
        "page_oracle": page_oracle(
            references, page_by_key, valid_by_id, reanchored_by_id
        ),
        "context_token_counts": {
            "min": min(token_counts.values(), default=0),
            "mean": fmean(token_counts.values()) if token_counts else 0,
            "median": median(token_counts.values()) if token_counts else 0,
            "max": max(token_counts.values(), default=0),
            "at_4000_limit": sum(value == 4000 for value in token_counts.values()),
        },
        "retrieval_config": {
            key: value
            for key, value in run["config"].items()
            if key not in ("inputs", "pages", "hg_lookup", "run_at", "gpu", "versions")
        },
    }
    private = {
        "matchable_quotes_by_id": {
            qid: quote_key_rows(keys) for qid, keys in valid_by_id.items()
        },
        "unmatchable_quotes_by_id": {
            qid: [
                [book_id, page, quote, reason]
                for (book_id, page, quote), reason in sorted(rows.items())
            ]
            for qid, rows in invalid_by_id.items()
            if rows
        },
        "reanchored_quotes_by_id": {
            qid: [
                [book_id, page, quote, anchors]
                for (book_id, page, quote), anchors in sorted(rows.items())
            ]
            for qid, rows in reanchored_by_id.items()
            if rows
        },
        "original_pdf_checks_by_id": source_check_by_id,
        "scores_by_id": score_by_id,
    }
    return questions, references, splits, summary, private


@cache
def pdf_text(book_id: str, page_number: int):
    path = DATA_DIR / "documents" / f"{book_id}.pdf"
    if not path.is_file():
        return {"pypdf_plain": "", "poppler_plain": ""}
    plain = PdfReader(path).pages[page_number - 1].extract_text() or ""
    poppler = subprocess.run(
        [
            "pdftotext",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            str(path),
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return {"pypdf_plain": plain, "poppler_plain": poppler}


def page_oracle(references, page_by_key, valid_by_id, reanchored_by_id):
    all_found, all_exact, all_anchored, all_expected, group_count, source_misses = (
        0,
        0,
        0,
        0,
        0,
        0,
    )
    questions_with_source_miss = 0
    token_counts = []
    for qid, reference in references.items():
        expected = citation_keys(reference)
        missing_pages = {
            (book_id, page)
            for book_id, page, _ in expected
            if (book_id, page) not in page_by_key
        }
        source_misses += len(missing_pages)
        questions_with_source_miss += bool(missing_pages)
        ranked_pages = []
        seen = set()
        for group in reference.get("evidence_groups", ()):
            for item in group:
                key = item["book_id"], item["page"]
                if key in seen or key not in page_by_key:
                    continue
                seen.add(key)
                ranked_pages.append((page_by_key[key], 0.0))
        found, token_count = context_quote_keys(ranked_pages, expected)
        text_by_page = context_page_text(ranked_pages)
        exact = found & valid_by_id[qid]
        anchored = {
            key
            for key, anchors in reanchored_by_id[qid].items()
            if any(
                source_anchors_present(text, anchors)
                for text in text_by_page.get(key[:2], ())
            )
        }
        all_expected += len(expected)
        all_exact += len(exact)
        all_anchored += len(anchored)
        all_found += len(exact | anchored)
        group_count += sum(
            bool(group) for group in reference.get("evidence_groups", ())
        )
        token_counts.append(token_count)
    return {
        "all_gold_pages_contextualized": True,
        "questions_with_missing_pages": questions_with_source_miss,
        "missing_page_references": source_misses,
        "gold_citations": all_expected,
        "strict_exact_citations_found": all_exact,
        "whole_line_reanchored_citations_found": all_anchored,
        "all_auditable_citations_found": all_found,
        "gold_pages_only_auditable_quote_recall_micro": all_found / all_expected
        if all_expected
        else None,
        "strict_exact_quote_recall_micro": all_exact / all_expected
        if all_expected
        else None,
        "unmatchable_gold_citations": all_expected - all_exact - all_anchored,
        "evidence_groups": group_count,
        "max_context_tokens": max(token_counts, default=0),
    }


def question_stats_and_overlap():
    full_references = read_json(FULL_COVERAGE / "references.json")
    blind_references = read_json(PERSONAL_BLIND / "blind_references.json")
    full = {row["id"]: row for row in full_references}
    blind = {row["id"]: row for row in blind_references}
    full_quotes = set().union(*(citation_keys(row) for row in full.values()))
    full_cases = set().union(*(case_keys(row) for row in full.values()))
    blind_quotes = {qid: citation_keys(row) for qid, row in blind.items()}
    blind_occurrences = {
        qid: [
            (item["book_id"], item["page"], _normalize(item["quote"]))
            for group in row.get("evidence_groups", ())
            for item in group
            if item.get("quote")
        ]
        for qid, row in blind.items()
    }
    overlap_question = sum(
        bool(values & full_quotes) for values in blind_quotes.values()
    )
    overlap_all = sum(
        bool(values) and values <= full_quotes for values in blind_quotes.values()
    )
    overlap_citations = sum(
        len(values & full_quotes) for values in blind_quotes.values()
    )
    blind_cases = {qid: case_keys(row) for qid, row in blind.items()}
    shared_case_count = len(set().union(*blind_cases.values()) & full_cases)
    action_overlap = defaultdict(set)
    for qid, row in blind.items():
        action_overlap[row["expected_action"]].update(blind_cases[qid] & full_cases)
    action_counts = Counter(row["expected_action"] for row in blind.values())
    action_case_overlap = {
        action: {
            "questions": action_counts[action],
            "questions_with_case_overlap": sum(
                bool(blind_cases[qid] & full_cases)
                for qid, row in blind.items()
                if row["expected_action"] == action
            ),
            "overlapping_case_keys": len(keys),
        }
        for action, keys in action_overlap.items()
    }
    return {
        "full_coverage_questions": len(full),
        "personal_blind_questions": len(blind),
        "personal_blind_citations": sum(
            len(values) for values in blind_quotes.values()
        ),
        "personal_blind_citation_occurrences": sum(
            len(values) for values in blind_occurrences.values()
        ),
        "blind_questions_with_any_exact_citation_in_full_coverage": overlap_question,
        "blind_questions_with_all_citations_in_full_coverage": overlap_all,
        "blind_exact_citation_overlap_count": overlap_citations,
        "blind_citation_occurrences_found_in_full_coverage": sum(
            key in full_quotes
            for values in blind_occurrences.values()
            for key in values
        ),
        "blind_unique_case_keys_shared_with_full_coverage": shared_case_count,
        "action_case_overlap": action_case_overlap,
    }


def case_keys(reference):
    keys = set(reference.get("case_keys", ()))
    if reference.get("case_id"):
        keys.add(reference["case_id"])
    return keys


def select_review_seeds(questions, references, splits):
    selected = []
    by_mode = defaultdict(list)
    for qid, reference in references.items():
        if splits[qid] != "synth_adv" or qid.startswith("SYF"):
            continue
        notes = reference.get("grading_notes", "")
        mode = (
            "bm25"
            if "(bm25)" in notes
            else "random"
            if "(random)" in notes
            else "unknown"
        )
        by_mode[mode].append(qid)
    for mode, rows in sorted(by_mode.items()):
        rows.sort(key=lambda qid: sha256(f"{SEED}:two:{qid}".encode()).hexdigest())
        selected.extend((qid, "two_document", mode) for qid in rows[:5])

    multi = defaultdict(list)
    for qid, reference in references.items():
        if splits[qid] != "synth_multi_adv" or qid.startswith("SYF"):
            continue
        note = reference.get("grading_notes", "")
        match = re.search(r"합성 다문서\((\d+)개, ([^)]+)\)", note)
        if not match:
            raise ValueError("다문서 평가 문항의 생성 유형을 읽을 수 없습니다.")
        stratum = f"{match.group(1)}_documents:{match.group(2)}"
        multi[stratum].append(qid)
    for stratum, rows in sorted(multi.items()):
        rows.sort(key=lambda qid: sha256(f"{SEED}:multi:{qid}".encode()).hexdigest())
        selected.extend((qid, "multi_document", stratum) for qid in rows[:5])

    false_premise_ids = [
        qid for qid in references if splits[qid] == "false_premise_adv"
    ]
    false_premise_ids.sort(
        key=lambda qid: sha256(f"{SEED}:false_premise:{qid}".encode()).hexdigest()
    )
    selected.extend(
        (qid, "false_premise", "independent") for qid in false_premise_ids[:10]
    )

    blind_questions = {
        row["id"]: row["question"]
        for row in read_json(PERSONAL_BLIND / "blind_questions.json")
    }
    blind_references = {
        row["id"]: row for row in read_json(PERSONAL_BLIND / "blind_references.json")
    }
    by_action = defaultdict(list)
    for qid, reference in blind_references.items():
        by_action[reference["expected_action"]].append(qid)
    for action, count in (("answer", 5), ("clarify", 5), ("abstain", 5)):
        candidates = by_action.get(action, [])
        candidates.sort(
            key=lambda qid: sha256(f"{SEED}:blind:{action}:{qid}".encode()).hexdigest()
        )
        selected.extend((qid, "personal_blind", action) for qid in candidates[:count])

    if len(selected) != 55:
        raise ValueError(
            f"검수 묶음이 55개 입력 항목이어야 합니다. 실제 {len(selected)}"
        )
    return selected, blind_questions, blind_references


def write_review_packet(
    questions,
    references,
    splits,
    rankings,
    page_by_key,
    output_root,
    comparison_pages_by_id=None,
):
    output = output_root / "review_packet"
    if output.exists():
        raise FileExistsError(
            f"검수 묶음이 이미 있습니다. 기존 파일을 덮어쓰지 않습니다. {output}"
        )
    selected, blind_questions, blind_references = select_review_seeds(
        questions, references, splits
    )
    expanded = []
    for qid, kind, stratum in selected:
        if kind in ("two_document", "multi_document"):
            false_id = f"SYF{qid[2:]}"
            if false_id not in references or citation_keys(
                references[qid]
            ) != citation_keys(references[false_id]):
                raise ValueError("검수용 정상·거짓 전제 쌍의 근거가 같지 않습니다.")
            pair_group = f"PAIR{len(expanded) // 2 + 1:03d}"
            expanded.append((qid, kind, stratum, pair_group))
            expanded.append((false_id, kind, stratum, pair_group))
        else:
            expanded.append((qid, kind, stratum, None))
    all_reviews, review_keys, all_source_pages = [], [], {}
    option_labels = defaultdict(dict)
    used = set()
    for qid, kind, stratum, pair_group in expanded:
        ref = references.get(qid, blind_references.get(qid))
        question = questions.get(qid, blind_questions.get(qid))
        if ref is None or question is None:
            raise ValueError("검수 항목의 질문이나 참고 파일이 없습니다.")
        blind_id = f"Q{len(all_reviews) + 1:03d}"
        all_reviews.append({"review_id": blind_id, "question": question})
        hidden = {
            "review_id": blind_id,
            "source_id": qid,
            "source_set": kind,
            "stratum": stratum,
            "expected_action": ref.get("expected_action"),
            "expected_behavior": ref.get("expected_behavior"),
            "evidence_groups": ref.get("evidence_groups", ()),
            "case_keys": sorted(case_keys(ref)),
            "pair_group": pair_group,
        }
        review_keys.append(hidden)
        source_keys = {
            (item["book_id"], item["page"])
            for group in ref.get("evidence_groups", ())
            for item in group
        }
        ranked_keys = []
        for row in rankings.get(qid, ()):
            key = (row["book_id"], row["page"])
            if key not in source_keys and len(source_keys) >= 20:
                break
            ranked_keys.append(key)
            source_keys.add(key)
        source_keys.update((comparison_pages_by_id or {}).get(qid, ()))
        if not ranked_keys and len(source_keys) < 20:
            seed_books = sorted({book for book, _ in source_keys})
            candidates = [
                key
                for key in page_by_key
                if key[0] in seed_books and key not in source_keys
            ]
            candidates.sort(
                key=lambda key: sha256(
                    f"{SEED}:{blind_id}:random-distractor:{key}".encode()
                ).hexdigest()
            )
            source_keys.update(candidates[: 20 - len(source_keys)])
        if not source_keys:
            raise ValueError("검수 후보로 쓸 페이지가 없습니다.")
        ordered_keys = sorted(
            source_keys,
            key=lambda key: sha256(f"{SEED}:{blind_id}:{key}".encode()).hexdigest(),
        )
        label_by_key = {
            key: f"{blind_id}-P{index:02d}"
            for index, key in enumerate(ordered_keys, start=1)
        }
        option_labels[blind_id] = label_by_key
        for key in ordered_keys:
            if key in used:
                continue
            used.add(key)
            page = page_by_key.get(key)
            if page is None:
                raise ValueError("검수용 후보 페이지가 로컬 캐시에 없습니다.")
            all_source_pages[key] = {
                "book_id": key[0],
                "page": key[1],
                "text": page["text"],
            }

    # Shuffle once so strata and paired variants remain hidden from reviewers.
    random.Random(SEED).shuffle(all_reviews)
    review_order = {row["review_id"]: index for index, row in enumerate(all_reviews)}
    review_keys.sort(key=lambda row: review_order[row["review_id"]])
    write_jsonl(output / "questions_blind.jsonl", all_reviews)
    write_jsonl(output / "answer_key_private.jsonl", review_keys)
    write_jsonl(
        output / "candidate_pages_private.jsonl",
        [
            {
                "review_id": review_id,
                "options": [
                    {"page_label": label, **all_source_pages[key]}
                    for key, label in option_labels[review_id].items()
                ],
            }
            for review_id in [row["review_id"] for row in all_reviews]
        ],
    )
    blank_reviews = [
        {
            "review_id": row["review_id"],
            "question_clear": "",
            "target_identifiable_from_question": "",
            "missing_disambiguating_fields": "",
            "needed_candidate_page_labels": "",
            "unneeded_candidate_page_labels": "",
            "question_natural_for_work": "",
            "notes": "",
        }
        for row in all_reviews
    ]
    write_jsonl(output / "review_form_blank.jsonl", blank_reviews)
    write_review_instructions(output)
    return {
        "blind_question_count": len(all_reviews),
        "seed_count": len(selected),
        "paired_variant_count": len(expanded) - len(selected),
        "selected_source_page_count": len(all_source_pages),
        "strata": dict(Counter(row["stratum"] for row in review_keys)),
        "packet_directory": str(output.relative_to(PROJECT_ROOT)),
        "paired_variant_groups_private": len(
            {row["pair_group"] for row in review_keys if row["pair_group"]}
        ),
        "comparison_packed_page_entries_added": sum(
            len((comparison_pages_by_id or {}).get(qid, ())) for qid, *_ in expanded
        ),
        "comparison_source_ids_with_packed_pages": sum(
            bool((comparison_pages_by_id or {}).get(qid)) for qid, *_ in expanded
        ),
    }


def write_review_instructions(directory: Path):
    instructions = """# Advanced 질문·근거 사람 검수 묶음

55개 표본 seed에 synthetic 쌍 문항 30개를 더한 85개 질문을, 질문 생성기·기대 답·기준선 점수를 모르는 두 사람이 별도로 검토한다. 먼저 `questions_blind.jsonl`만 보고 질문이 자연스럽고 대상이 하나로 식별되는지 판단한다. 두 번째 단계에서는 진행 담당자가 `candidate_pages_private.jsonl`의 동일 ID와 질문을 보여준다. 후보 표기는 무작위이며 정답 쪽이나 검색 순위 표시는 없다. 각 페이지가 요청 사실에 필요한지 평가한다. 모든 필수 사실과 검토 의견은 원본 페이지 및 질문과 대조한다.

각 검토자는 `review_form_blank.jsonl`을 복사해 작성한다. `answer_key_private.jsonl`은 두 검토가 제출된 뒤에만 비교한다. 두 사람이 서로의 판단을 보지 않은 상태로 완료하고, 불일치는 원문을 함께 다시 읽어 해결한다. 예상 행동이 보류인 문항은 관련 정보가 PDF 안에 실제로 있는지 함께 확인한다. 이 묶음은 대표성 검증을 위한 사람 표본이다. 빈도 추정에는 쓰지 않는다.

## 사람이 질문을 먼저 쓰는 별도 holdout

`human_authored_holdout_template.jsonl`의 빈 항목에 실제 업무 상황을 참고해 질문을 원문 페이지를 보기 전에 작성한다. 작성자는 질문에 대한 정답 근거를 사전에 읽지 않는다. 질문 완료 뒤 담당 검수자가 PDF에서 모든 유효 답변에 필요한 원본 페이지와 인용을 찾아 `reference_after_question_template.jsonl`에 기록한다. 여러 답이 합법적으로 성립하면 각각의 동등한 근거 묶음을 기입한다. 두 검토자는 필요 근거와 질문이 충분히 구체적인지 판정한다. 질문 세트와 정답을 같은 작성자가 먼저 보고 서로 맞추지 않도록 한다.

짝 문항 연결은 개인용 `answer_key_private.jsonl`에만 남아 있다. 원본 질문과 근거 텍스트가 있어 이 디렉터리 전체를 개인 실험 산출물로 로컬에만 둔다.
"""
    (directory / "README.md").write_text(instructions, encoding="utf-8")
    write_jsonl(
        directory / "human_authored_holdout_template.jsonl",
        [
            {
                "human_item_id": f"H{i:03d}",
                "question_written_before_source_review": "",
                "request_intent": "",
            }
            for i in range(1, 21)
        ],
    )
    write_jsonl(
        directory / "reference_after_question_template.jsonl",
        [
            {
                "human_item_id": f"H{i:03d}",
                "expected_action": "",
                "evidence_groups": [],
                "accepted_alternative_evidence_groups": [],
                "source_verification": "",
                "human_reviewers": [],
                "adjudication_notes": "",
            }
            for i in range(1, 21)
        ],
    )


def select_peer_pairs(references, splits):
    normal_ids = [
        qid
        for qid in references
        if splits[qid] in ("synth_adv", "synth_multi_adv") and not qid.startswith("SYF")
    ]
    by_stratum = defaultdict(list)
    for qid in normal_ids:
        reference = references[qid]
        if splits[qid] == "synth_adv":
            mode = (
                "random" if "random" in reference.get("grading_notes", "") else "bm25"
            )
            stratum = f"2doc:{mode}"
        else:
            note = reference.get("grading_notes", "")
            match = re.search(r"합성 다문서\((\d+)개, ([^)]+)\)", note)
            if not match:
                raise ValueError("다문서 검수 층화용 유형을 읽을 수 없습니다.")
            stratum = f"{match.group(1)}doc:{match.group(2)}"
        by_stratum[stratum].append(qid)
    selected = []
    quota = {"2doc:random": 5, "2doc:bm25": 5}
    quota.update({f"{n}doc:{mode}": 5 for n in (3, 4) for mode in ("anchor", "chain")})
    for stratum, count in quota.items():
        rows = by_stratum.get(stratum, [])
        if len(rows) < count:
            raise ValueError(f"표본 층 {stratum}이 {count}개보다 적습니다.")
        rows.sort(
            key=lambda qid: sha256(f"{SEED}:{stratum}:{qid}".encode()).hexdigest()
        )
        selected.extend((qid, stratum) for qid in rows[:count])
    return selected


def load_comparison_pages(path: Path | None, page_by_key):
    if path is None:
        return {}, None
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"비교 벤치 순위 파일을 찾을 수 없습니다: {path}")
    pages_by_id = defaultdict(set)
    row_count, packed_entries = 0, 0
    for row in read_jsonl(path):
        if not all(key in row for key in ("id", "arm", "pool", "expanded", "packed")):
            raise ValueError(
                "비교 순위 행에 id/arm/pool/expanded/packed 필드가 필요합니다."
            )
        row_count += 1
        for item in row["packed"]:
            key = item["book_id"], item["page"]
            if key not in page_by_key:
                raise ValueError(
                    "비교 순위에 로컬 원본 페이지 캐시에 없는 페이지가 있습니다."
                )
            pages_by_id[row["id"]].add(key)
            packed_entries += 1
    if not row_count:
        raise ValueError("비교 순위 파일에 행이 없습니다.")
    return dict(pages_by_id), {
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "rows": row_count,
        "ids": len(pages_by_id),
        "packed_page_entries": packed_entries,
    }


def write_outputs(output: Path, comparison_rankings: Path | None = None):
    output = output.resolve()
    artifacts_root = (PROJECT_ROOT / "artifacts").resolve()
    if not output.is_relative_to(artifacts_root):
        raise ValueError("출력 경로는 저장소의 ignored artifacts/ 아래여야 합니다.")
    if output.exists():
        raise FileExistsError(
            f"기존 감사 결과를 보존합니다. 새 출력 경로를 지정하세요. {output}"
        )
    output.mkdir(parents=True)
    questions, references, splits, peer_summary, peer_private = audit_peer()
    personal_overlap = question_stats_and_overlap()
    pages = json.loads(CACHE_PATH.read_bytes())
    page_by_key = {location(page): page for page in pages}
    comparison_pages_by_id, comparison_summary = load_comparison_pages(
        comparison_rankings, page_by_key
    )
    rankings = load_rankings()[1]
    review_summary = write_review_packet(
        questions,
        references,
        splits,
        rankings,
        page_by_key,
        output,
        comparison_pages_by_id,
    )

    selected_seeds = select_peer_pairs(references, splits)
    calibration = []
    for normal_id, stratum in selected_seeds:
        false_id = f"SYF{normal_id[2:]}"
        if false_id not in references:
            raise ValueError("쌍의 거짓 전제 변형이 누락됐습니다.")
        if citation_keys(references[normal_id]) != citation_keys(references[false_id]):
            raise ValueError("한 쌍이 같은 근거 페이지를 공유하지 않습니다.")
        calibration.extend(
            {"question_id": qid, "seed_id": normal_id, "stratum": stratum}
            for qid in (normal_id, false_id)
        )
    write_jsonl(output / "paired_synthetic_sample_private.jsonl", calibration)
    write_json(output / "peer_quote_audit_private.json", peer_private)
    peer_summary["independent_source_bundle_counts"] = {
        split: {
            "questions": sum(value == split for value in splits.values()),
            "distinct_evidence_bundles": len(
                {
                    frozenset(
                        (item["book_id"], item["page"])
                        for group in reference["evidence_groups"]
                        for item in group
                    )
                    for qid, reference in references.items()
                    if splits[qid] == split
                }
            ),
        }
        for split in sorted(set(splits.values()))
    }
    peer_summary["normal_vs_false_premise"] = pair_comparison(references, splits)
    peer_summary["human_review_packet"] = review_summary
    if comparison_summary is not None:
        peer_summary["comparison_rankings_input"] = comparison_summary
    peer_summary["personal_300_vs_6500_overlap"] = personal_overlap
    write_json(output / "audit_summary.json", peer_summary)
    print(json.dumps(peer_summary, ensure_ascii=False, indent=2))


def pair_comparison(references, splits):
    qrows, ref_by_id = {}, references
    for split in ("synth_adv", "synth_multi_adv"):
        qrows[split] = [qid for qid, value in splits.items() if value == split]
    result = {}
    for split, qids in qrows.items():
        pairs = []
        for normal_id in qids:
            if normal_id.startswith("SYF"):
                continue
            false_id = f"SYF{normal_id[2:]}"
            if false_id not in ref_by_id:
                continue
            normal = ref_by_id[normal_id]
            false = ref_by_id[false_id]
            same_gold = citation_keys(normal) == citation_keys(false)
            normal_ids = set(ID_PATTERN.findall(normal["question"]))
            false_ids = set(ID_PATTERN.findall(false["question"]))
            pairs.append(
                {
                    "same_gold_citations": same_gold,
                    "normal_identifiers": len(normal_ids),
                    "false_premise_identifiers": len(false_ids),
                }
            )
        result[split] = {
            "pairs": len(pairs),
            "same_gold_citations": sum(pair["same_gold_citations"] for pair in pairs),
            "identifier_count_strata": dict(
                Counter(
                    f"{pair['normal_identifiers']}->{pair['false_premise_identifiers']}"
                    for pair in pairs
                )
            ),
            "normal_at_least_two_false_at_most_one_identifier": sum(
                pair["normal_identifiers"] >= 2
                and pair["false_premise_identifiers"] <= 1
                for pair in pairs
            ),
        }
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Offline audit of Advanced quote anchors and a blind human-review packet."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT,
        help=f"new local output directory under artifacts/ (default: {OUTPUT.relative_to(PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--comparison-rankings",
        type=Path,
        help="local benchmark rankings.jsonl; adds packed pages to each matching blind candidate pool",
    )
    args = parser.parse_args()
    write_outputs(args.output, args.comparison_rankings)


if __name__ == "__main__":
    main()
