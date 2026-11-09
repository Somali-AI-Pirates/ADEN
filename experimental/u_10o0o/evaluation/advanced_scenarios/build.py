"""Build the local Advanced scenario benchmark from verified source records."""

from collections import Counter, defaultdict
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from app.evaluation.dataset import load_questions, load_references
from app.generation.context import build_context
from app.ingestion.curation import read_rows
from app.ingestion.layout import identifiers
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_hg_lookup import CASE_INDEX, normalized
from experimental.u_10o0o.advanced_patterns import location, write_json
from experimental.u_10o0o.advanced_search import PEER_DATA, PEER_SPLITS
from experimental.u_10o0o.advanced_usage_blind import (
    EVAL,
    FACTS,
    FULL,
    PREVIOUS,
    SPECS,
    answer_pools,
    candidate,
    exception_pools,
    hg_fact,
    load_inputs,
    make_reference,
    reference_locations,
    source_evidence,
)
from experimental.u_10o0o.advanced_workflow_eval import storage_amount_matches_record
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from experimental.u_10o0o.prepare import CACHE_PATH, PROJECT_ROOT

OUT = Path(__file__).resolve().parent
SEED = "advanced-scenarios-20261109-v1"
SPLITS = {
    "retrieval": (1, 2, 3, 4, 5, 6),
    "deferred_answer": (7,),
    "negative_control": (8,),
}
SITUATIONS = {
    1: "직접 조회",
    2: "번호 없이 한 사건 식별",
    3: "한 사건의 여러 기록 연결",
    4: "서로 다른 사건 비교",
    5: "적용되는 기록·조건 판정",
    6: "대상이 모호해 추가 확인 필요",
    7: "대상은 명확하지만 확정 근거 부족",
    8: "자료 범위 밖",
}
FIELDS = {
    "free": "최초 계약의 보관료 무료일수",
    "gate": "실제 적입 반출일",
    "scheduled": "예약해 둔 반납일",
    "returned": "지정 장소에 실제로 반납한 날",
    "approval": "변경 조건의 승인 기록",
    "paid": "보관료가 붙은 일수",
    "charged": "확정 보관료 청구액",
    "received": "수납 기록에 남은 입금액",
    "late_charge": "반납지연료의 확정 상태",
}
NEGATIVES = {
    "unrelated": (
        "집에서 바질을 기를 때 잎이 누렇게 변하는 원인이 뭐야?",
        "초등학생이 분수의 크기를 비교하는 방법을 예로 설명해줘.",
        "피아노에서 왼손 반주를 처음 연습할 때 어떤 순서로 하면 돼?",
        "베란다에서 토마토 모종을 옮겨 심기에 좋은 시기를 알려줘.",
        "파이썬에서 리스트와 튜플의 차이를 간단히 설명해줘.",
        "가정용 커피 드리퍼로 두 잔을 내릴 때 물은 얼마나 쓰면 돼?",
        "집의 와이파이가 방마다 약해지는 이유를 알려줘.",
        "삼각형의 세 내각을 더하면 180도가 되는 이유가 뭐야?",
        "소설을 쓸 때 1인칭과 3인칭 시점의 효과를 비교해줘.",
        "달의 위상이 한 달 동안 어떻게 바뀌는지 설명해줘.",
    ),
    "near_domain": (
        "지금 부산항 신항으로 들어오는 선박의 실시간 AIS 좌표를 확인해줘.",
        "오늘 지정 장치장 앞 대기 차량이 몇 대인지 CCTV로 세어줄래?",
        "지금 접수한 장비 반납 예약이 터미널 앱에서 승인됐는지 조회해줘.",
        "이번 달 우리 회사의 미결 청구 합계를 사내 회계 시스템에서 가져와줘.",
        "외부 선사의 오늘 현물 운임 API를 조회해서 현재 달러 운임을 알려줘.",
        "지금 항만 출입 게이트가 통제 중인지 운영 관제 화면을 확인해줘.",
        "우리 회사 은행 계좌에서 오늘 실제로 송금된 금액을 조회해줘.",
        "업로드하지 않은 지난달 외부 터미널의 서명된 반납 증빙을 열어줘.",
        "이 파일 묶음이 만들어진 뒤 사내 시스템에서 승인된 정정 기록을 찾아줘.",
        "내일 현장 작업자별 교대표를 인사 시스템에서 조회해줘.",
    ),
}


def fingerprint(path):
    return {
        "path": str(path.resolve().relative_to(PROJECT_ROOT)),
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def order(key):
    return sha256(f"{SEED}/{key}".encode()).digest()


def build():
    if any(
        (OUT / f"{split}_questions.json").exists()
        or (OUT / f"{split}_references.json").exists()
        for split in SPLITS
    ):
        raise FileExistsError("새 출력 디렉터리에서 실행하세요.")

    usage = EVAL / "advanced_usage_blind/local"
    runs = PROJECT_ROOT / "artifacts/u_10o0o"
    earlier = (
        *PREVIOUS,
        *(PEER_DATA / f"{name}.json" for name in PEER_SPLITS.values()),
        *(usage / f"{name}_references.json" for name in ("blind", "pilot")),
        *(
            runs / name / "references.json"
            for name in (
                "advanced-workflow-20261109-data-checked",
                "advanced-pair-holdout-20261109-data",
                "advanced-vocabulary-20261109-data-verified",
            )
        ),
    )
    earlier_refs = [r for path in earlier for r in load_references(path).values()]
    reserved_pages = set().union(*(reference_locations(r) for r in earlier_refs))
    reserved_cases = {
        key
        for r in earlier_refs
        for key in (r.get("case_id"), *r.get("case_keys", ()))
        if key
    }
    prior_questions = {
        normalized(r["question"])
        for r in earlier_refs + list(load_references(FULL).values())
        if r.get("question")
    }
    for name in (
        "advanced-workflow-20261109-data-checked",
        "advanced-pair-holdout-20261109-data",
        "advanced-vocabulary-20261109-data-verified",
    ):
        prior_questions.update(
            normalized(q["question"])
            for q in load_questions(runs / name / "questions.json")
        )

    restored, cases, display, sources, records, typed, ha, ha_rows, handbook = (
        load_inputs()
    )
    eligible = [
        key
        for key, pages in cases.items()
        if key not in reserved_cases
        and not any(location(p) in reserved_pages for p in pages)
    ]
    roles = {
        (source["book_id"], source["page"]): row["record_role"]
        for row in read_rows(CASE_INDEX / "case_sources.jsonl")
        for source in (sources[row["source_id"]],)
    }
    for by_role in ha.values():
        roles.update(
            {
                (source["book_id"], source["page"]): role
                for role, source in by_role.items()
            }
        )
    used_cases, used_pages, used_questions = set(), set(), set(prior_questions)
    selected = defaultdict(list)

    def source_pages(item):
        return {
            (source["book_id"], source["page"])
            for fact in item["facts"]
            for row in (fact.record, *fact.support)
            for source in (sources[row["source_id"]],)
        }

    def add(group, item, variant, *, documents=None):
        keys = set(item["case_keys"])
        pages = source_pages(item)
        question = normalized(item["question"])
        count = len({book for book, _ in pages})
        if (
            keys & (used_cases | reserved_cases)
            or pages & (used_pages | reserved_pages)
            or question in used_questions
            or (documents is not None and count != documents)
        ):
            return False
        evidence = [source_evidence(f, sources, restored) for f in item["facts"]]
        if not all(evidence):
            return False
        qid = f"AS-S{group:02}-{len(selected[group]) + 1:02}"
        ref = make_reference(qid, item, evidence)
        unique = {
            tuple(sorted(cite.items())): cite for bundle in evidence for cite in bundle
        }
        # The standard quote evaluator treats alternatives within a group as OR.
        # These are mandatory citations, so each gets its own group.
        ref["evidence_groups"] = [[cite] for cite in unique.values()]
        for requirement in ref["evidence_requirements"]:
            cite = requirement["primary"][0]
            requirement["role"] = roles.get(
                (cite["book_id"], cite["page"]), "업무 자료집"
            )
        context = build_context(
            _context_candidates([(restored[key], 1.0) for key in sorted(pages)]),
            8,
            8000,
            _count_tokens,
        )
        if (
            len(pages) != len(context.final_chunks)
            or not fact_coverage(ref, context)["all_facts"]
        ):
            raise ValueError(f"{qid} 필수 원문을 실제 문맥에서 확인할 수 없습니다.")
        for book in {book for book, _ in pages}:
            remaining = [
                row for row in context.final_chunks if row.chunk.book_id != book
            ]
            if fact_coverage(ref, remaining)["all_facts"]:
                raise ValueError(f"{qid} 필수가 아닌 PDF를 정답으로 포함했습니다.")
        ref.update(
            {
                "scenario": group,
                "scenario_name": SITUATIONS[group],
                "variant": variant,
                "evaluation_stage": "deferred_answer" if group == 7 else "retrieval",
                "required_document_count": count,
                "required_page_count": len(pages),
                "case_count": len(keys),
                "identifier_count": len(identifiers(item["question"])),
                "gold_context_tokens": context.token_count,
            }
        )
        selected[group].append(({"id": qid, "question": item["question"]}, ref))
        used_cases.update(keys)
        used_pages.update(pages)
        used_questions.add(question)
        return True

    def take(group, n, pool, variant, *, documents=None):
        before = len(selected[group])
        for item in pool:
            add(group, item, variant, documents=documents)
            if len(selected[group]) == before + n:
                print(f"상황 {group} · {variant} · {n}문항", flush=True)
                return
        raise ValueError(
            f"상황 {group} · {variant} · {len(selected[group]) - before}/{n}문항"
        )

    def case_order(tag):
        return sorted(eligible, key=lambda key: order(f"{tag}/{key}"))

    def fact(key, code):
        found = hg_fact(typed, records, key, code)
        if (
            found
            and code == "charged"
            and not storage_amount_matches_record(found.record)
        ):
            return None
        return replace(found, ask=FIELDS[code]) if found else None

    def single(tag, mode, codes, render, *, check=lambda _: True):
        for key in case_order(tag):
            facts = tuple(fact(key, code) for code in codes)
            if all(facts) and check(facts):
                question = render(key, facts)
                if question:
                    yield candidate(
                        "hg", [key], tag, mode, key, facts, question=question
                    )

    # Reserve shared-clue cases first so other cohorts cannot consume half a pair.
    ambiguous, deferred = exception_pools(
        cases, display, typed, records, reserved_pages, reserved_cases
    )
    good_pairs = [
        item
        for item in ambiguous
        if all(fact(key, "charged") for key in item["case_keys"])
    ]
    take(
        6,
        20,
        sorted(good_pairs, key=lambda i: order(i["question"])),
        "두 사건이 같은 화주·규격·실제 반출일 공유",
    )

    for state in ("receipt_missing", "non_designated_yard_only"):
        choices = []
        for item in deferred:
            key = item["case_keys"][0]
            if hg_fact(typed, records, key, "returned", states=(state,)) is None:
                continue
            choices.append(
                item
                | {
                    "question": f"{key} 건 정산을 마감하려고 해. 지정 장소에 실제로 반납한 날짜와 반납지연료를 확정할 수 있어?",
                    "style": "explicit",
                }
            )
        take(7, 10, sorted(choices, key=lambda i: order(i["question"])), state)

    for family, n in (("ha", 6), ("handbook", 6)):
        pools = answer_pools(
            {},
            {},
            sources,
            records,
            typed,
            ha if family == "ha" else {},
            ha_rows,
            handbook if family == "handbook" else {},
            reserved_pages,
            reserved_cases,
        )
        take(
            1,
            n,
            sorted(pools[family, 1], key=lambda i: order(i["question"])),
            family,
            documents=1,
        )
    for code in ("gate", "returned", "charged", "received"):
        take(
            1,
            2,
            single(
                f"직접 {code}",
                "explicit",
                (code,),
                lambda key, _: f"{key} 건 {FIELDS[code]} 기록을 확인해줘.",
            ),
            code,
            documents=1,
        )

    profiles = defaultdict(list)
    dated = defaultdict(list)
    for key, profile in display.items():
        profiles[
            tuple(normalized(profile[name]) for name in ("shipper", "cargo", "spec"))
        ].append(key)
        gate = fact(key, "gate")
        if gate:
            dated[(normalized(profile["shipper"]), profile["spec"], gate.value)].append(
                key
            )

    def indirect(key):
        profile = display[key]
        terms = tuple(
            normalized(profile[name]) for name in ("shipper", "cargo", "spec")
        )
        if len(profiles[terms]) == 1:
            return f"{profile['shipper']}의 {profile['cargo']} 화물이 든 {SPECS[profile['spec']]} 건"
        return None

    def dated_question(key, facts):
        profile = display[key]
        day = facts[0].value
        if len(dated[normalized(profile["shipper"]), profile["spec"], day]) == 1:
            return f"{day}에 화물을 싣고 밖으로 나간 {profile['shipper']}의 {SPECS[profile['spec']]} 건을 찾고 있어. 실제 반출 기록과 보관료 청구액을 확인해줘."
        return None

    for code in ("gate", "charged"):
        take(
            2,
            5,
            single(
                f"번호 없는 {code}",
                "profile",
                (code,),
                lambda key, _: (
                    f"{subject}, {FIELDS[code]}을 확인해줘."
                    if (subject := indirect(key))
                    else None
                ),
            ),
            f"화주·화물·규격 / {code}",
            documents=1,
        )
    take(
        2,
        10,
        single(
            "날짜와 단서로 찾기", "dated_profile", ("gate", "charged"), dated_question
        ),
        "화주·규격·실제 반출일",
        documents=2,
    )

    for codes, prompt in (
        (
            ("charged", "received"),
            "회계 인계용으로 보관료 청구액과 실제 수납 기록을 같이 확인해줘.",
        ),
        (
            ("free", "approval", "charged"),
            "보관료를 검토 중이야. 최초 무료일수, 변경 조건 승인 기록, 확정 청구액을 이어서 확인해줘.",
        ),
        (
            ("free", "gate", "approval", "charged"),
            "청구 근거를 인계해야 해. 최초 무료일수와 실제 적입 반출일, 변경 조건 승인 기록, 확정 보관료를 정리해줘.",
        ),
        (
            ("free", "gate", "approval", "charged", "received"),
            "비용 감사에 넘길 자료가 필요해. 최초 무료일수부터 실제 반출일, 변경 승인, 확정 보관료, 수납 기록까지 한 건으로 정리해줘.",
        ),
    ):
        take(
            3,
            5,
            single(
                f"{len(codes)}개 기록 연결",
                "explicit",
                codes,
                lambda key, _: f"{key} 건. {prompt}",
            ),
            f"{len(codes)}개 PDF",
            documents=len(codes),
        )

    for variant, count in (("both_explicit", 8), ("mixed", 6), ("both_indirect", 6)):
        for i in range(count):
            codes = (("charged",), ("gate", "charged"), ("paid", "charged"))[i % 3]
            tag = f"{variant}/{i}"
            pool = [
                key
                for key in case_order(tag)
                if key not in used_cases and all(fact(key, code) for code in codes)
            ]
            asks = "와 ".join(FIELDS[code] for code in codes)
            completed = False
            for left in pool:
                a = left if variant != "both_indirect" else indirect(left)
                if not a:
                    continue
                for right in pool:
                    b = right if variant == "both_explicit" else indirect(right)
                    if (
                        left == right
                        or not b
                        or display[left]["shipper"] == display[right]["shipper"]
                    ):
                        continue
                    facts = tuple(
                        replace(fact(key, code), ask=f"{side}의 {FIELDS[code]}")
                        for side, key in (("첫 번째 건", left), ("두 번째 건", right))
                        for code in codes
                    )
                    item = candidate(
                        "hg",
                        [left, right],
                        "서로 다른 사건 비교",
                        variant,
                        a,
                        facts,
                        question=f"첫 번째는 {a}, 두 번째는 {b}이야. 두 건의 {asks}을 각각 확인해서 비교해줘.",
                    )
                    if add(4, item, variant):
                        completed = True
                        break
                if completed:
                    break
            if not completed:
                raise ValueError(
                    f"두 사건 비교 {tag}의 원문 근거를 구성할 수 없습니다."
                )
        print(f"상황 4 · {variant} · {count}문항", flush=True)

    take(
        5,
        8,
        single(
            "예정과 실제",
            "explicit",
            ("scheduled", "returned"),
            lambda key, fs: (
                f"{key} 건을 {fs[0].value}에 실제 반납했다고 인계받았어. 예약일과 지정 장소 실제 반납일을 대조해줘."
            ),
            check=lambda fs: fs[0].value != fs[1].value,
        ),
        "예정일을 실제일로 오인",
    )
    take(
        5,
        6,
        single(
            "최초 약정과 승인",
            "explicit",
            ("free", "approval"),
            lambda key, _: (
                f"{key} 건 무료기간 변경 요청이 들어왔다고 들었어. 최초 약정의 무료일수와 변경에 대한 승인 기록을 확인해줘."
            ),
        ),
        "요청과 승인 구분",
    )
    take(
        5,
        6,
        single(
            "청구와 수납",
            "explicit",
            ("charged", "received"),
            lambda key, _: (
                f"{key} 건 인수인계에 보관료 청구액을 실제 입금액으로 적어도 될까? 청구 명세와 수납 기록을 각각 확인해줘."
            ),
            check=lambda fs: fs[0].value != fs[1].value,
        ),
        "청구액과 수납액 구분",
    )

    for label, questions in NEGATIVES.items():
        for question in questions:
            qid = f"AS-S08-{len(selected[8]) + 1:02}"
            selected[8].append(
                (
                    {"id": qid, "question": question},
                    {
                        "id": qid,
                        "scenario": 8,
                        "scenario_name": SITUATIONS[8],
                        "variant": label,
                        "evaluation_stage": "negative_control",
                        "evidence_required": False,
                        "evidence_groups": [],
                        "evidence_requirements": [],
                        "required_document_count": 0,
                        "required_page_count": 0,
                        "case_keys": [],
                        "case_count": 0,
                        "identifier_count": 0,
                        "expected_action": "abstain",
                        "accepted_action_labels": ["abstain"],
                        "expected_behavior": "Advanced 자료의 근거로 답을 뒷받침할 수 없다.",
                        "reference_answer": "제공된 Advanced 자료의 범위 밖이다.",
                    },
                )
            )

    if set(selected) != set(SITUATIONS) or any(
        len(rows) != 20 for rows in selected.values()
    ):
        raise ValueError("여덟 상황에 20문항씩 필요합니다.")
    files = {}
    for split, groups in SPLITS.items():
        rows = [row for group in groups for row in selected[group]]
        for suffix, values in (
            ("questions", [q for q, _ in rows]),
            ("references", [r for _, r in rows]),
        ):
            path = OUT / f"{split}_{suffix}.json"
            write_json(path, values)
            files[path.name] = fingerprint(path)["sha256"]
    write_json(
        OUT / "manifest.json",
        {
            "seed": SEED,
            "total_questions": 160,
            "splits": {
                split: {"scenarios": groups, "questions": 20 * len(groups)}
                for split, groups in SPLITS.items()
            },
            "scenarios": {
                group: {
                    "name": name,
                    "variants": Counter(r["variant"] for _, r in selected[group]),
                    "required_pdf_counts": Counter(
                        r["required_document_count"] for _, r in selected[group]
                    ),
                }
                for group, name in SITUATIONS.items()
            },
            "source_checks": {
                "positive_and_deferred_questions": 140,
                "distinct_selected_source_pages": len(used_pages),
                "distinct_selected_source_pdfs": len({book for book, _ in used_pages}),
                "verified_max_gold_context_tokens": max(
                    r["gold_context_tokens"]
                    for group in range(1, 8)
                    for _, r in selected[group]
                ),
                "excluded_earlier_cases": len(reserved_cases),
                "excluded_earlier_pages": len(reserved_pages),
                "human_review": "pending",
            },
            "deferred_note": "7번은 LLM 연결 후 답변 평가에 사용한다. 이번 검색 집계에서는 제외한다.",
            "negative_control_note": "8번은 별도 집계하며 검색 Recall 분모에서 제외한다.",
            "source_inputs": [
                fingerprint(path)
                for path in (
                    CACHE_PATH,
                    CASE_INDEX / "case_sources.jsonl",
                    FACTS / "role_facts.jsonl",
                    FULL,
                    *earlier,
                )
            ],
            "files_sha256": files,
            "generator_sha256": fingerprint(Path(__file__))["sha256"],
            "external_api_calls": 0,
        },
    )
    print("확정 · 검색 120 · 답변 보류 20 · 범위 밖 대조 20", flush=True)


if __name__ == "__main__":
    build()
