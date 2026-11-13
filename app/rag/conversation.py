"""Bounded chat history and the decision used before the first retrieval."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.rag.records import codes

MAX_HISTORY_TURNS = 8
RECENT_TURNS = 2
EXCLUSION_MARKERS = ("제외", "말고", "빼고", "아닌")


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=800)
    answer: str = Field(min_length=1, max_length=1000)


class ConversationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["standalone", "resolved", "needs_history", "needs_clarification"]
    search_question: str
    turn_ids: list[int]


@dataclass(frozen=True)
class ConversationContext:
    search_question: str
    turns: tuple[ConversationTurn, ...] = ()
    needs_clarification: bool = False


async def resolve_conversation(
    question: str,
    history: Sequence[ConversationTurn],
    interpret: Callable[
        [str, dict[int, ConversationTurn], bool], Awaitable[ConversationDecision]
    ],
) -> ConversationContext:
    """Try the recent exchange first and expand the window at most once."""
    turns = tuple(enumerate(history[-MAX_HISTORY_TURNS:], start=1))
    visible = dict(turns[-RECENT_TURNS:])
    decision = await interpret(question, visible, len(turns) > len(visible))
    if decision.status == "needs_history" and len(turns) > len(visible):
        visible = dict(turns)
        decision = await interpret(question, visible, False)

    if decision.status == "standalone":
        return ConversationContext(question)

    if any(turn_id not in visible for turn_id in decision.turn_ids):
        return ConversationContext(question, needs_clarification=True)

    selected_ids = set(decision.turn_ids)
    selected = tuple(
        turn for turn_id, turn in visible.items() if turn_id in selected_ids
    )
    if decision.status != "resolved":
        return ConversationContext(question, selected, needs_clarification=True)

    search_question = decision.search_question.strip()
    current_codes = set(codes(question))
    search_codes = set(codes(search_question))
    allowed_codes = current_codes | {
        code for turn in selected for code in codes(f"{turn.question}\n{turn.answer}")
    }
    if (
        not selected
        or not search_question
        or len(search_question) > max(2000, len(question))
        or not current_codes <= search_codes <= allowed_codes
        or (
            any(marker in question for marker in EXCLUSION_MARKERS)
            and not any(marker in search_question for marker in EXCLUSION_MARKERS)
        )
    ):
        return ConversationContext(question, selected, needs_clarification=True)
    return ConversationContext(search_question, selected)
