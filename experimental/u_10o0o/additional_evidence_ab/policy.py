"""Experimental planner instructions and previews; no evaluation inputs."""

import json

EXCERPT_TOKENS = 384
EXTRA_INSTRUCTIONS = """
종료 전 근거 확인:
- 원질문의 서로 다른 요구를 구분한다. 요청처, 전달할 항목, 적용 조건을 물으면 각각의 근거를 확인한다.
  질문하지 않은 마감시간이나 개별 사건의 진행상태를 새 요구로 만들지 않는다.
- 담당자 연락처나 처리 사례만으로 제출 절차 전체를 확인했다고 판단하지 않는다.
  절차 질문은 적용 가능한 업무 지침에서 필요한 전달 대상과 전달 항목이 확인되어야 충분하다.
  아직 빠진 조치나 조건이 있으면 그 부분만 묻는 짧은 질의로 search_evidence를 사용한다.
  원질문 전체를 조금 바꿔 반복하거나 이미 해결한 요구에 집중하지 않는다.
- 다른 규격, 최초 제출과 정정, 시행일 이전 접수와 이후 신규 접수를 구분한다.
  일반 절차를 묻는 질문에 사건번호가 없다는 이유만으로 추가 확인을 요구하지 않는다.
- candidate_previews는 이미 찾았지만 본문을 전달하지 않은 후보의 서두다.
  낮은 순위라도 남은 요구의 지침·예외가 보이면 read_sources로 해당 원문을 확인한다.
  이 서두만 보고 검증된 원문처럼 인용하지 않는다. 이미 읽은 비슷한 사례를 다시 읽는 대신
  필요한 새 지침을 읽는다. 필요한 후보가 없으면 빠진 절차·조건만 검색한다.
- finish.selected_sources에는 각 요구를 뒷받침하는 원문과 적용 규정을 우선 남긴다.
  이미 확인한 동일 종류 사례로 자리를 채워 다른 요구의 근거를 빠뜨리지 않는다.
  요구한 근거가 남아 있지 않으면 sufficient로 종료하지 않는다.
"""


def _observed_sources(value):
    result = set()
    if isinstance(value, dict):
        for name in ("passages", "pages"):
            for page in value.get(name, []):
                if (
                    isinstance(page, dict)
                    and page.get("text")
                    and page.get("source_id")
                ):
                    result.add(page["source_id"])
        for item in value.values():
            result.update(_observed_sources(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_observed_sources(item))
    return result


def with_previews(history, page_lookup, encoding):
    """Copy messages; expose only previously discovered, not-yet-shown pages."""
    decoded = []
    for message in history:
        try:
            value = (
                json.loads(message.content)
                if isinstance(message.content, str)
                else None
            )
        except (ValueError, TypeError):
            value = None
        decoded.append(value)
    observed = set().union(*(_observed_sources(x) for x in decoded))
    previewed = set()
    audit = []

    def visit(value):
        if isinstance(value, dict):
            for child in list(value.values()):
                visit(child)
            sources = value.get("candidate_sources")
            if not isinstance(sources, list):
                return
            previews = []
            for source in sources:
                if (
                    not isinstance(source, str)
                    or source in observed
                    or source in previewed
                ):
                    continue
                document = page_lookup(source)
                if document is None:
                    continue
                tokens = encoding.encode(document.page_content, disallowed_special=())
                excerpt = encoding.decode(tokens[:EXCERPT_TOKENS])
                previews.append(
                    {
                        "source_id": source,
                        "text": excerpt,
                        "preview_only": True,
                        "excerpt_truncated": len(tokens) > EXCERPT_TOKENS,
                    }
                )
                previewed.add(source)
                audit.append(
                    {"source_id": source, "tokens": min(len(tokens), EXCERPT_TOKENS)}
                )
            if previews:
                value["candidate_previews"] = previews
        elif isinstance(value, list):
            for child in value:
                visit(child)

    messages = []
    for message, value in zip(history, decoded, strict=True):
        if value is None:
            messages.append(message)
            continue
        visit(value)
        messages.append(
            message.model_copy(
                update={"content": json.dumps(value, ensure_ascii=False)}
            )
        )
    return messages, audit


def configure_model(model):
    """Rebind only this experiment's planner; product source stays untouched."""
    from langchain_core.messages import SystemMessage
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from app.rag.model import PLAN_OUTPUT_TOKENS

    model.plan_prompt += "\n" + EXTRA_INSTRUCTIONS
    prompt = ChatPromptTemplate.from_messages(
        [SystemMessage(content=model.plan_prompt), MessagesPlaceholder("history")]
    )
    model._plan = {
        finish_only: prompt
        | model.model.bind_tools(
            [model.tools[-1]] if finish_only else model.tools,
            tool_choice="finish" if finish_only else "required",
            strict=True,
            parallel_tool_calls=False,
            max_output_tokens=PLAN_OUTPUT_TOKENS,
        )
        for finish_only in (False, True)
    }
