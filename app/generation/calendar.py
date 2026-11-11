"""검색 원문의 실제 사건·산입 규칙으로 날짜를 열거한다. 금액·승인 효력은 계산하지 않는다."""

import json
import re
from datetime import date, datetime, timedelta

from app.ingestion.common_rules import match_common_rule
from app.ingestion.layout import CASE_ID, compact, identifiers
from shared.contracts import ContextBundle, ModelAnswer

EVENTS = {
    "actual_discharge": "실제양하일은",
    "actual_loaded_gate_out": "실제적입장비반출일은",
    "actual_empty_return": "실제공장비반납일은",
}
WEEKDAYS = "월화수목금토일"


def counted_dates(start: date, end: date, *, weekdays_only: bool) -> list[date]:
    """양 끝을 포함한다. 다른 포함·제외 조건은 호출자가 먼저 적용한다."""
    days = (end - start).days
    if days < 0 or days >= 366:
        raise ValueError("산입 구간은 순서가 맞는 366일 이내 기간이어야 합니다.")
    result = []
    for i in range(days + 1):
        day = start + timedelta(days=i)
        if not weekdays_only or day.weekday() < 5:
            result.append(day)
    return result


def _requested_costs(question: str) -> set[str]:
    """마지막의 명시적 비용 한정·제외·전체 지정을 반영한다."""
    costs = {"보관료", "장비반납지연료"}
    fee = r"장비반납지연료|반납지연료|보관료"
    pattern = (rf"(?P<only>{fee})(?:부분)?만(?:설명|알려|검산|계산|확인|답변|답해|조회)"
               rf"|(?P<excluded>{fee})(?:만|를|은|는)?(?:제외|빼고)"
               r"|(?P<all>두비용(?:을)?모두|두비용다|전체비용|둘다)")
    for match in re.finditer(pattern, compact(question)):
        if match["all"]:
            costs = {"보관료", "장비반납지연료"}
        else:
            label = match["only"] or match["excluded"]
            label = "장비반납지연료" if label == "반납지연료" else label
            costs = ({label} if match["only"] else {"보관료", "장비반납지연료"} - {label})
    # shortcut: only explicit cost requests are parsed; extend wording with question fixtures.
    return costs


def _calendar_periods(question: str, context: ContextBundle) -> dict:
    """확인 가능한 단일 예약·장비만 지원한다. 부족·충돌 자료에는 계산표를 만들지 않는다."""
    if not re.search(r"계산|검산|산입|무료일|유료일|무료날짜|유료날짜", compact(question)):
        return {}
    ids = identifiers(question)
    cases = [value for value in ids if CASE_ID.match(value)]
    equipment = [value for value in ids if value.startswith(("HL-", "EDU-"))]
    if len(cases) != 1 or len(equipment) != 1:
        return {}
    chunks = []
    for number, item in enumerate(context.final_chunks, 1):
        source = f"S{number}"
        citation = context.source_map.get(source)
        if citation is None or (citation.book_id, citation.file_name, citation.page) != (
            item.chunk.book_id, item.chunk.file_name, item.chunk.page
        ):
            return {}
        text = item.chunk.heading + "\n" + item.chunk.text
        if set(cases + equipment) <= set(identifiers(text)):
            chunks.append((source, compact(item.chunk.text)))
    rules, event_values = {}, {name: {} for name in EVENTS}
    for source, text in chunks:
        start = text.find("보관료는실제양하일")
        stop = text.find("반납지연료는달력일기준이다", start)
        if start >= 0 and stop >= 0:
            clause = text[start:stop + len("반납지연료는달력일기준이다")]
            match = match_common_rule(clause)
            if match and match["kind"] == "period_rule":
                params = match["parameters"]
                if params["storage"]["day_basis"] != "weekdays" or "별도공휴일제외는없다" in clause:
                    identity = json.dumps(params, sort_keys=True)
                    rules.setdefault(identity, (params, []))[1].append(source)
        for name, label in EVENTS.items():
            for raw in re.findall(label + r"(\d{4}-\d{2}-\d{2})(?!\d)", text):
                try:
                    parsed = date.fromisoformat(raw)
                except ValueError:
                    return {}
                event_values[name].setdefault(parsed, []).append(source)
    # shortcut: only the existing source-matched period grammar is supported; add formats with source fixtures.
    if len(rules) != 1:
        return {}
    params, rule_sources = next(iter(rules.values()))
    cutoffs = set(re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", question))
    if len(cutoffs) > 1:
        return {}
    try:
        cutoff = date.fromisoformat(next(iter(cutoffs))) if cutoffs else date.max
    except ValueError:
        return {}
    periods = {}
    free_days = _unchanged_free_days(question, chunks)
    requested = _requested_costs(question)
    for kind, label in (("storage", "보관료"), ("return_delay", "장비반납지연료")):
        if label not in requested:
            continue
        rule = params[kind]
        start_values, end_values = event_values[rule["start_event"]], event_values[rule["end_event"]]
        if len(start_values) != 1 or len(end_values) != 1:
            continue
        start_event, end_event = next(iter(start_values)), next(iter(end_values))
        if start_event > end_event or max(start_event, end_event) > cutoff:
            continue
        try:
            start = start_event + timedelta(days=rule["start_offset_days"])
            end = end_event - timedelta(days=not rule["end_inclusive"])
            days = [] if end < start else counted_dates(start, end, weekdays_only=rule["day_basis"] == "weekdays")
        except (ValueError, OverflowError):
            continue
        sources = list(dict.fromkeys(rule_sources + start_values[start_event] + end_values[end_event]))
        periods[label] = {"dates": days, "sources": sources, "weekdays_only": rule["day_basis"] == "weekdays"}
        if label in free_days:
            count, free_sources = free_days[label]
            periods[label].update(free=days[:count], paid=days[count:],
                                  free_sources=list(dict.fromkeys(sources + free_sources)))
    return periods


def _unchanged_free_days(question: str, chunks: list[tuple[str, str]]) -> dict:
    """동일 조회 시각의 '추가 변경 없음; 최초 계약 적용' 원문이 있을 때만 무료일을 확정한다."""
    asof = set(re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2}\d{2}:\d{2})KST", compact(question)))
    if len(asof) != 1:
        return {}
    try:
        datetime.strptime(next(iter(asof)), "%Y-%m-%d%H:%M")
    except ValueError:
        return {}
    contracts = {value for _, text in chunks for value in identifiers(text) if value.startswith("CT-")}
    if len(contracts) != 1:
        return {}
    approvals = []
    for source, text in chunks:
        if "승인대조" not in text:
            continue
        times = set(re.findall(r"대조기준(\d{4}-\d{2}-\d{2}\d{2}:\d{2})KST", text))
        if "승인대조추가변경없음;최초계약적용" not in text or times != asof:
            return {}
        approvals.append(source)
    if not approvals:
        return {}
    result = {}
    for label in ("보관료", "장비반납지연료"):
        values = {}
        for source, text in chunks:
            if "비용별최초계약조건" not in text or "기본무료" not in text:
                continue
            for raw in re.findall(label + r"(\d+)일", text):
                values.setdefault(int(raw), []).append(source)
        if len(values) != 1:
            return {}
        count, sources = next(iter(values.items()))
        result[label] = (count, sources + approvals)
    # shortcut: approval changes need effective-date rules; only explicit unchanged terms are automated.
    return result


def _date_list(days: list[date], *, weekdays_only: bool) -> str:
    if len(days) > 7:
        basis = " (월~금)" if weekdays_only else ""
        return f"{days[0].isoformat()}~{days[-1].isoformat()}{basis}"
    return ", ".join(f"{day.isoformat()}({WEEKDAYS[day.weekday()]})" for day in days) or "없음"


def _parse_date_list(value: str, *, weekdays_only: bool) -> list[date]:
    if value == "없음":
        return []
    dates = []
    for token in value.split(","):
        token = token.strip()
        filtered = token.endswith("(월~금)")
        if filtered:
            if not weekdays_only:
                raise ValueError("달력일 구간에서 주말을 제외할 수 없습니다.")
            token = token.removesuffix("(월~금)").strip()
        endpoints = token.split("~")
        if len(endpoints) not in (1, 2) or (filtered and len(endpoints) != 2):
            raise ValueError("날짜 또는 양끝을 포함한 구간을 표시해야 합니다.")
        parsed = []
        for endpoint in endpoints:
            match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:\(([월화수목금토일])(?:요일)?\))?", endpoint.strip())
            if match is None:
                raise ValueError("날짜 형식이 맞지 않습니다.")
            day = date.fromisoformat(match[1])
            if match[2] and match[2] != WEEKDAYS[day.weekday()]:
                raise ValueError("요일이 맞지 않습니다.")
            parsed.append(day)
        dates.extend(parsed if len(parsed) == 1 else counted_dates(parsed[0], parsed[1], weekdays_only=filtered))
        if len(dates) > 366:
            raise ValueError("산입 날짜가 지원 기간을 초과합니다.")
    return dates


def calendar_reference(question: str, context: ContextBundle) -> str:
    """확인된 산입 날짜와, 조건을 확정한 경우 무료·유료 날짜를 모델에 전달한다."""
    periods = _calendar_periods(question, context)
    if not periods:
        return ""
    lines = []
    required = False
    for label, period in periods.items():
        marks = "".join(f"[{source}]" for source in period["sources"])
        lines.append(f"{label} {marks}: 산입 {len(period['dates'])}일; 산입 날짜: {_date_list(period['dates'], weekdays_only=period['weekdays_only'])}.")
        if "free" in period:
            required = True
            marks = "".join(f"[{source}]" for source in period["free_sources"])
            for key, kind in (("free", "무료"), ("paid", "유료")):
                lines.append(f"{label} {kind} 날짜: {_date_list(period[key], weekdays_only=period['weekdays_only'])} {marks}")
    notes = ("\n\n로컬 날짜 산입 대조표\n"
             "아래는 제공 자료의 실제 사건과 계약 원문 산입 규칙을 로컬 달력으로 계산한 결과입니다. 승인 변경의 효력은 별도로 확인하세요.\n"
             + "\n".join(lines)
             + "\n유효한 무료기간과 승인 변경을 원문에서 확인한 뒤, 위 산입 날짜 순서로 무료일과 유료일을 배정하세요."
             " 7일을 넘는 날짜 목록은 양끝을 포함한 YYYY-MM-DD~YYYY-MM-DD 구간으로 표시하세요. 월~금만 산입하는 구간에는 반드시 (월~금)을 붙이세요."
             " 유효한 변경 기록이 산입 구간이나 산입 규칙을 바꾸면 이 표를 그대로 적용하지 마세요."
             " 이 표만으로 승인 효력·무료일수·단가·금액을 확정하지 마세요.")
    if required:
        notes += ("\n무료·유료 날짜 행은 동일 조회 시각의 변경 없음 기록과 최초 계약 무료일수까지 대조한 결과입니다."
                  " answered이면 answer 본문에 위 '비용명 무료 날짜: ...'와 '비용명 유료 날짜: ...' 행을 각각 한 줄씩 포함하세요."
                  " 짧은 목록은 YYYY-MM-DD(요일)로 나열하고, 긴 목록은 위와 같이 구간으로 표시하세요. 해당 날짜가 없으면 '없음'이라고 쓰세요."
                  " 구간은 양끝을 포함하며 (월~금)이 없으면 주말도 포함합니다. 일부 날짜를 생략하거나 (월~금) 표시를 빼지 마세요. 날짜 행에는 자료 ID 외 설명을 섞지 마세요."
                  " 이 대조표의 생성 과정은 답변에서 설명하지 마세요.")
    return notes


def validate_calendar_dates(answer: ModelAnswer, question: str, context: ContextBundle) -> None:
    """확정 가능한 날짜 행의 누락·요일·무료/유료 배정·중복을 검사한다."""
    text = answer.answer.replace("**", "").replace("`", "")
    for label, period in _calendar_periods(question, context).items():
        if "free" not in period:
            continue
        for key, kind in (("free", "무료"), ("paid", "유료")):
            field = f"{label} {kind} 날짜"
            rows = re.findall(rf"(?m)^\s*(?:[-*]\s*)?{re.escape(field)}\s*:\s*([^\n]*)$", text)
            if not rows and answer.status != "answered":
                continue
            if len(rows) != 1:
                raise ValueError(f"날짜 대조: {field} 행을 한 번 표시해야 합니다.")
            value = re.sub(r"\[S\d+\]", "", rows[0]).strip().removesuffix(".").strip()
            try:
                dates = _parse_date_list(value, weekdays_only=period["weekdays_only"])
            except ValueError as error:
                raise ValueError(f"날짜 대조: {field}의 날짜 형식·구간·요일이 맞지 않습니다.") from error
            if dates != period[key]:
                raise ValueError(f"날짜 대조: {field}가 계약·실제 사건으로 계산한 날짜와 다릅니다.")
