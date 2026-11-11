"""평가 질문·참고 정답 로드.

실행 단계는 `load_questions()`로 id·question만 읽는다. 정답·근거·expected_action은
답변과 최종 문맥을 저장한 뒤 `load_references()`로 평가 단계에서만 읽는다.

shortcut: 실제 Basic 평가 JSON 구조를 아직 확인하지 못해 목록 / {"<키>": 목록} / JSONL을 받는다.
실제 파일을 열어본 뒤 구조가 다르면 `_items()`만 맞추면 된다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def _items(path: Path) -> list[Mapping[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        data: Any = []
        for line in text.splitlines():
            if line.strip():
                data.append(json.loads(line))
    else:
        data = json.loads(text)
    if isinstance(data, Mapping):
        lists = []
        for key, value in data.items():
            if isinstance(value, list) and not key.startswith("_"):
                lists.append(value)
        if len(lists) != 1:
            raise ValueError(f"{path}: 문항 목록을 찾을 수 없다(최상위 목록 키가 1개여야 함)")
        data = lists[0]
    is_object_list = isinstance(data, list)
    if is_object_list:
        for item in data:
            if not isinstance(item, Mapping):
                is_object_list = False
                break
    if not is_object_list:
        raise ValueError(f"{path}: 문항은 객체 목록이어야 한다")
    return data


def load_questions(path: str | Path, ids: Iterable[str] | None = None) -> list[dict[str, str]]:
    """id·question만 반환한다. ids를 주면 그 순서대로 선택하고 없는 ID는 오류."""
    items = _items(Path(path))
    questions = {}
    for item in items:
        qid, question = item.get("id"), item.get("question")
        if not isinstance(qid, str) or not isinstance(question, str) or not question.strip():
            raise ValueError(f"id/question이 없는 문항: {item.get('id')!r}")
        if qid in questions:
            raise ValueError(f"중복 문항 ID: {qid}")
        questions[qid] = {"id": qid, "question": question}
    if ids is None:
        return list(questions.values())
    ids = list(ids)
    unknown = []
    for requested_id in ids:
        if requested_id not in questions:
            unknown.append(requested_id)
    if unknown:
        raise ValueError(f"질문 파일에 없는 ID: {unknown}")
    selected = []
    for requested_id in ids:
        selected.append(questions[requested_id])
    return selected


def load_references(path: str | Path) -> dict[str, dict[str, Any]]:
    """평가 전용. id → 참고 항목 전체(정답·근거·채점 기준·expected_action 등)."""
    references = {}
    for item in _items(Path(path)):
        references[str(item["id"])] = dict(item)
    return references


def evidence_books(reference: Mapping[str, Any]) -> list[str]:
    """evidence_groups 안의 모든 book_id를 모아 중복 제거한다(그룹 구조 깊이에 무관)."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            book = node.get("book_id")
            if isinstance(book, str) and book:
                found.add(book)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(reference.get("evidence_groups") or [])
    return sorted(found)
