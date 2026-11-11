"""Fielded BM25, evidence-role ordering, and bounded lexical follow-up.

This experiment consumes only the original page cache. It does not load evaluation
questions, reference evidence, generated answers, or entity-link labels.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from experimental.u_10o0o.bm25 import BM25Index, load_tokenizer

TOKENIZER_NAME = "bigram-id-normalized"
FIELD_POOL = 80
HEADING_WEIGHT = 0.25
HEADING_BLOCK_LIMIT = 3
HEADING_CHAR_LIMIT = 600

ROLE_LOOKAHEAD = 10
MAX_FOLLOWUP_ADDITIONS = 3
FOLLOWUP_SEARCH_K = 100

# Cues describe kinds of source evidence. These are intentionally generic and are
# used only when words appear in a runtime question or source page text.
ROLE_TERMS: dict[str, tuple[str, ...]] = {
    "contract_terms": (
        "계약",
        "요율",
        "운임",
        "요금",
        "보관료",
        "반납지연료",
        "무료기간",
        "과금",
        "약관",
        "조건",
        "기준",
        "contract",
        "rate",
        "tariff",
    ),
    "operational_event": (
        "반입",
        "반출",
        "반납",
        "예약",
        "입항",
        "출항",
        "작업",
        "실적",
        "일자",
        "시각",
        "처리",
        "상태",
        "완료",
        "event",
        "operation",
    ),
    "approval_change": (
        "신청",
        "승인",
        "결재",
        "요청",
        "변경",
        "정정",
        "확정",
        "취소",
        "통지",
        "회신",
        "접수",
        "confirmation",
        "approval",
        "amendment",
    ),
    "billing_record": (
        "청구",
        "계산",
        "검산",
        "입금",
        "지급",
        "영수",
        "비용",
        "금액",
        "산출",
        "정산",
        "invoice",
        "billing",
        "payment",
        "amount",
    ),
}

# Strict mode distinguishes document kinds using source headings and specific
# identifiers/names found in the source. Generic body mentions such as "비용",
# "일자", or "상태" do not assign a role.
STRICT_ROLE_CUES: dict[str, tuple[str, ...]] = {
    "contract_terms": (
        "계약요율",
        "계약 조건",
        "계약서",
        "요율표",
        "운임표",
        "보관료",
        "반납지연료",
        "무료기간",
        "tariff",
        "contract rate",
    ),
    "operational_event": (
        "반입기록",
        "반출기록",
        "반납기록",
        "입항기록",
        "출항기록",
        "예약·장비",
        "운항문의",
        "작업실적",
        "작업기록",
        "실제 반입",
        "실제 반출",
        "실제 반납",
        "arrival record",
        "operation log",
    ),
    "approval_change": (
        "신청·승인기록",
        "승인기록",
        "승인번호",
        "수정통지",
        "정정통지",
        "변경요청",
        "결재",
        "approval record",
        "amendment notice",
    ),
    "billing_record": (
        "청구번호",
        "청구내역",
        "청구서",
        "입금기록",
        "계산메모",
        "검산기록",
        "검산번호",
        "정산서",
        "청구금액",
        "invoice",
        "payment record",
    ),
}

STRICT_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "contract_terms": (
        "계약",
        "요율",
        "운임",
        "보관료",
        "반납지연료",
        "무료기간",
        "tariff",
    ),
    "operational_event": (
        "반입",
        "반출",
        "반납",
        "입항",
        "출항",
        "작업실적",
        "운항기록",
    ),
    "approval_change": (
        "신청",
        "승인",
        "결재",
        "수정통지",
        "정정통지",
        "변경요청",
    ),
    "billing_record": (
        "청구",
        "계산",
        "검산",
        "입금",
        "지급",
        "영수",
        "정산",
        "invoice",
    ),
}

_ROLE_ORDER = tuple(ROLE_TERMS)
_FOOTER = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
_BLOCKS = re.compile(r"\n\s*\n+")


def _contains_cue(text: str, cues: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(cue.casefold() in lowered for cue in cues)


def _page_heading(text: str) -> str:
    """Return a small, source-derived leading field from the cached PDF layout."""
    blocks = [part.strip() for part in _BLOCKS.split(text.strip()) if part.strip()]
    if blocks and _FOOTER.fullmatch(blocks[-1]):
        blocks.pop()
    if len(blocks) > 1:
        selected = blocks[:HEADING_BLOCK_LIMIT]
    else:
        # Some layout extractions have no blank-line boundaries. In that case,
        # keep only the first few lines instead of indexing a body-sized field.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines and _FOOTER.fullmatch(lines[-1]):
            lines.pop()
        selected = [" ".join(lines[:6])]
    return "\n".join(selected)[:HEADING_CHAR_LIMIT]


def _query_roles(question: str) -> list[str]:
    """Infer only roles explicitly cued by words in the user's question."""
    matches: list[tuple[int, str]] = []
    lowered = question.casefold()
    for role in _ROLE_ORDER:
        positions = [
            lowered.find(cue.casefold())
            for cue in ROLE_TERMS[role]
            if lowered.find(cue.casefold()) >= 0
        ]
        if positions:
            matches.append((min(positions), role))
    return [role for _, role in sorted(matches)]


def _page_roles(page: dict) -> set[str]:
    text = str(page.get("text", ""))
    heading = _page_heading(text)
    roles = {
        role
        for role, cues in ROLE_TERMS.items()
        if _contains_cue(heading, cues) or _contains_cue(text, cues)
    }
    return roles


class PatternIndex:
    """Indexes unchanged page objects for four retrieval-pattern experiments.

    `base` is the exact Bigram-ID BM25 ranking used by the current experiment.
    `fielded` boosts body candidates with a BM25 index over the leading blocks.
    `role_order` and `followup` are independent post-processing arms.
    """

    def __init__(self, pages: Sequence[dict]):
        if not pages:
            raise ValueError("페이지 목록이 비어 있습니다.")
        self.pages = list(pages)
        self.tokenizer, self.tokenizer_config = load_tokenizer(TOKENIZER_NAME)
        self.base_index = BM25Index(self.pages, tokenizer=self.tokenizer)
        self._page_index_by_id = {
            id(page): index for index, page in enumerate(self.pages)
        }

        self.heading_pages: list[dict] = []
        self._heading_page_by_id: dict[int, dict] = {}
        for page_index, page in enumerate(self.pages):
            heading = _page_heading(str(page.get("text", "")))
            if not self.tokenizer(heading):
                continue
            field_page = {
                "file_name": page.get("file_name", ""),
                "page": page.get("page"),
                "text": heading,
                "_page_index": page_index,
            }
            self.heading_pages.append(field_page)
            self._heading_page_by_id[id(field_page)] = page
        self.heading_index = (
            BM25Index(self.heading_pages, tokenizer=self.tokenizer)
            if self.heading_pages
            else None
        )
        self.config = {
            "tokenizer": self.tokenizer_config,
            "fielded": {
                "fusion": "max_normalized_bm25_heading_bonus",
                "field_pool": FIELD_POOL,
                "heading_weight": HEADING_WEIGHT,
                "heading_block_limit": HEADING_BLOCK_LIMIT,
                "heading_char_limit": HEADING_CHAR_LIMIT,
            },
            "roles": {
                "terms": ROLE_TERMS,
                "lookahead": ROLE_LOOKAHEAD,
                "strict_terms": STRICT_ROLE_CUES,
                "strict_query_terms": STRICT_QUERY_TERMS,
            },
            "followup": {
                "max_additions": MAX_FOLLOWUP_ADDITIONS,
                "search_k": FOLLOWUP_SEARCH_K,
            },
        }

    def base(self, question: str, *, top_k: int = 20) -> list[tuple[dict, float]]:
        """Run the original Bigram-ID BM25 baseline over the same page objects."""
        return self.base_index.search(question, top_k=top_k)

    def fielded(self, question: str, *, top_k: int = 20) -> list[tuple[dict, float]]:
        """Apply a bounded heading bonus to a deeper pool of body matches."""
        if top_k <= 0:
            return []
        body_ranked = self.base_index.search(question, top_k=max(FIELD_POOL, top_k))
        if not body_ranked:
            return []
        highest_body = body_ranked[0][1]
        scores = {id(page): score / highest_body for page, score in body_ranked}
        if self.heading_index is not None:
            heading_ranked = self.heading_index.search(question, top_k=FIELD_POOL)
            if heading_ranked:
                highest_heading = heading_ranked[0][1]
                for field_page, score in heading_ranked:
                    key = id(self._heading_page_by_id[id(field_page)])
                    if key in scores:
                        scores[key] += HEADING_WEIGHT * score / highest_heading
        ranked = [(page, scores[id(page)]) for page, _ in body_ranked]
        return sorted(ranked, key=lambda item: (-item[1], self._page_index(item[0])))[
            :top_k
        ]

    def role_order(
        self,
        question: str,
        ranked: Sequence[tuple[dict, float]],
        *,
        strict_roles: bool = False,
    ) -> list[tuple[dict, float]]:
        """Move the best explicit missing-role evidence within a bounded window.

        Original scores are retained. Roles are ordered by their first cue in the
        question; all unselected pages keep their relative order.
        """
        rows = list(ranked)
        if strict_roles:
            return self._strict_role_order(question, rows)

        requested = _query_roles(question)
        if len(requested) < 2 or not rows:
            return rows

        selected_ids: set[int] = set()
        selected: list[tuple[dict, float]] = []
        covered: set[str] = set()
        for role in requested:
            if role in covered:
                continue
            for page, score in rows[:ROLE_LOOKAHEAD]:
                if id(page) in selected_ids or role not in _page_roles(page):
                    continue
                selected.append((page, score))
                selected_ids.add(id(page))
                covered.add(role)
                break

        return selected + [row for row in rows if id(row[0]) not in selected_ids]

    def followup(
        self,
        question: str,
        ranked: Sequence[tuple[dict, float]],
        *,
        top_k: int = 20,
        strict_roles: bool = False,
    ) -> list[tuple[dict, float]]:
        """Add at most three role-specific candidates when requested roles are absent.

        The input must be the raw baseline candidate ranking, before cross-encoder
        reranking. The output stays at `top_k`, preserves original order, and only
        replaces the tail when an inferred evidence role is missing.
        """
        rows = list(ranked)
        if top_k <= 0:
            return []
        rows = rows[:top_k]
        requested = (
            _strict_query_roles(question) if strict_roles else _query_roles(question)
        )
        if not requested or not rows:
            return rows

        if strict_roles:
            present = {
                role for page, _ in rows if (role := _dominant_role(page)) is not None
            }
        else:
            present = set().union(*(_page_roles(page) for page, _ in rows))
        missing = [role for role in requested if role not in present]
        if not missing:
            return rows

        original_ids = {id(page) for page, _ in rows}
        additions: list[tuple[dict, float]] = []
        addition_ids: set[int] = set()
        for role in missing:
            if len(additions) >= min(MAX_FOLLOWUP_ADDITIONS, top_k):
                break
            cues = STRICT_ROLE_CUES[role] if strict_roles else ROLE_TERMS[role]
            expanded_query = f"{question} {' '.join(cues)}"
            candidates = self.base_index.search(
                expanded_query, top_k=min(FOLLOWUP_SEARCH_K, len(self.pages))
            )
            for page, score in candidates:
                if (
                    id(page) in original_ids
                    or id(page) in addition_ids
                    or role
                    not in (
                        {_dominant_role(page)} if strict_roles else _page_roles(page)
                    )
                ):
                    continue
                additions.append((page, score))
                addition_ids.add(id(page))
                break

        if not additions:
            return rows
        retained_count = max(0, top_k - len(additions))
        return rows[:retained_count] + additions

    def _strict_role_order(
        self, question: str, rows: list[tuple[dict, float]]
    ) -> list[tuple[dict, float]]:
        requested = _strict_query_roles(question)
        if not requested or len(rows) <= 3:
            return rows

        # KURE's selected head is fixed. Only the tail can be reordered, and a
        # role already represented in that head is considered covered.
        head, tail = rows[:3], rows[3:]
        covered = {
            role for page, _ in head if (role := _dominant_role(page)) is not None
        }
        selected: list[tuple[dict, float]] = []
        selected_ids: set[int] = set()
        for role in requested:
            if role in covered:
                continue
            for page, score in tail[:ROLE_LOOKAHEAD]:
                if id(page) in selected_ids or _dominant_role(page) != role:
                    continue
                selected.append((page, score))
                selected_ids.add(id(page))
                covered.add(role)
                break
        return head + selected + [row for row in tail if id(row[0]) not in selected_ids]

    def _page_index(self, target: dict) -> int:
        # BM25Index keeps source objects intact, so this is a cheap deterministic
        # tie-breaker and does not require adding metadata to the caller's pages.
        return self._page_index_by_id.get(id(target), len(self.pages))


def _strict_query_roles(question: str) -> list[str]:
    lowered = question.casefold()
    matches = []
    for role, cues in STRICT_QUERY_TERMS.items():
        positions = [
            lowered.find(cue.casefold())
            for cue in cues
            if lowered.find(cue.casefold()) >= 0
        ]
        if positions:
            matches.append((min(positions), role))
    return [role for _, role in sorted(matches)]


def _dominant_role(page: dict) -> str | None:
    """Return a role only when headings/strong source phrases identify one."""
    text = str(page.get("text", ""))
    heading = _page_heading(text).casefold()

    def count_hits(value: str) -> dict[str, int]:
        return {
            role: sum(value.count(cue.casefold()) for cue in cues)
            for role, cues in STRICT_ROLE_CUES.items()
        }

    heading_hits = count_hits(heading)
    if max(heading_hits.values(), default=0) > 0:
        highest = max(heading_hits.values())
        winners = [role for role, count in heading_hits.items() if count == highest]
        return winners[0] if len(winners) == 1 else None

    source_hits = count_hits(text.casefold())
    highest = max(source_hits.values(), default=0)
    winners = [
        role for role, count in source_hits.items() if count == highest and count
    ]
    return winners[0] if len(winners) == 1 else None
