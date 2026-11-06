"""Poppler layout 텍스트에서 근거 위치와 표 행을 보존한다."""

import re
from itertools import pairwise

FOOTER = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
HEADER_WORDS = re.compile(
    r"번호|기록|대상|항목|상태|일자|금액|기간|구간|장비|수량|원문|확인|청구|계약|사건|업무|기준|종류|요율|무료|근거|내용|자료|구분|비용|통화|범위|증빙|판본|성격|접수|처리|화주|예약|품명|터미널|보관료|반납지연료"
)
FIRST_HEADERS = {
    "처리기록",
    "기록번호",
    "업무기록번호",
    "대조번호",
    "신청·승인기록",
    "청구번호",
    "확인원문",
    "상태기록",
    "원문번호",
    "계약",
    "예약·장비",
    "계획번호",
    "VGM대조",
    "입금기록",
    "단위대조",
    "문서기록",
    "접수번호",
    "수정통지",
    "운항문의",
    "반입기록",
    "당사자등록",
    "등록번호",
    "인계번호",
    "계산메모",
    "접수대조",
    "검산번호",
    "검토기록번호",
    "확인번호",
    "SI기록",
    "항목",
    "비용",
    "구분",
    "기록",
    "확인항목",
    "자료",
    "기록종류",
    "화주",
}
IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9-])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+(?![A-Za-z0-9-])"
)
CASE_ID = re.compile(r"^(?:GR(?:26|27)-|HG20\d\d-|HA28-|SW28-|DL28-)")


def compact(text):
    return re.sub(r"\s+", "", text)


def readable(text):
    return " ".join(text.split())


def identifiers(text):
    return sorted(set(IDENTIFIER.findall(text)))


def blocks(text):
    """빈 줄로 나눈 블록과 layout 텍스트의 1부터 센 행 범위를 보존한다."""
    lines = text.splitlines()
    result, start = [], None
    for number, line in enumerate([*lines, ""], 1):
        if line.strip():
            if start is None:
                start = number
        elif start is not None:
            result.append(
                {
                    "start": start,
                    "end": number - 1,
                    "lines": lines[start - 1 : number - 1],
                }
            )
            start = None
    return result


def page_body(text, book_id, page, total):
    parts = blocks(text)
    if not parts or not FOOTER.fullmatch(parts[-1]["lines"][-1]):
        raise ValueError(f"페이지 꼬리말 없음: {book_id} p{page}")
    footer = FOOTER.fullmatch(parts[-1]["lines"][-1])
    if (int(footer[1]), int(footer[2])) != (page, total):
        raise ValueError(f"페이지 번호 불일치: {book_id} p{page}")
    parts.pop()
    if len(parts) < 2:
        raise ValueError(f"본문 없음: {book_id} p{page}")
    # Basic/IS 자료는 표지·제목·절, 계약 인계철은 표지 뒤 한 블록에 제목과 본문이 있다.
    heading_parts = [readable(" ".join(parts.pop(0)["lines"]))]
    if "|" in heading_parts[0]:
        while parts and len(heading_parts) < 3:
            heading_parts.append(readable(" ".join(parts.pop(0)["lines"])))
    else:
        first = parts[0]
        heading_parts.append(first["lines"][0].strip())
        if len(first["lines"]) > 1:
            first["lines"] = first["lines"][1:]
            first["start"] += 1
        else:
            parts.pop(0)
    return " > ".join(heading_parts), parts


def header_cells(block):
    if len(block["lines"]) != 1:
        return None
    text = block["lines"][0]
    spans = [
        (m.start(), m.end(), m.group())
        for m in re.finditer(r"\S(?:.*?\S)?(?= {2,}|$)", text)
    ]
    if not 2 <= len(spans) <= 8:
        return None
    if compact(spans[0][2]) not in FIRST_HEADERS:
        return None
    if sum(bool(HEADER_WORDS.search(label)) for _, _, label in spans) < 2:
        return None
    if any(len(label) > 22 or re.search(r"\d", label) for _, _, label in spans):
        return None
    return spans


def looks_like_row(block):
    return bool(re.search(r"\S {2,}\S", block["lines"][0]))


def parse_table(header, rows):
    """모든 행에서 비어 있는 열 경계만 사용한다. 애매하면 분리하지 않는다."""
    spans = header_cells(header)
    lines = [line for row in rows for line in row["lines"]]
    if not spans or not lines:
        return None
    width = max(map(len, lines))
    empty = [
        all(i >= len(line) or line[i].isspace() for line in lines) for i in range(width)
    ]
    gaps = []
    for match in re.finditer(r"1{2,}", "".join("1" if flag else "0" for flag in empty)):
        if match.start() and match.end() < width:
            gaps.append((match.start(), match.end()))
    cuts = [0]
    for (_, end, _), (start, _, _) in pairwise(spans):
        candidates = [
            (max(left, end), min(right, start))
            for left, right in gaps
            if max(left, end) < min(right, start)
        ]
        if not candidates:
            return None
        left, right = max(candidates, key=lambda pair: pair[1] - pair[0])
        cut = (left + right) // 2
        if cut <= cuts[-1]:
            return None
        cuts.append(cut)
    cuts.append(max(width, spans[-1][1]))
    labels = [readable(label) for _, _, label in spans]
    if len(set(labels)) != len(labels):
        return None
    result = []
    for row in rows:
        for line in row["lines"]:
            if compact(
                "".join(line[left:right] for left, right in pairwise(cuts))
            ) != compact(line):
                raise ValueError("표의 열 분리에서 원문 문자가 유실됐습니다.")
        values = [
            readable(" ".join(line[left:right].strip() for line in row["lines"]))
            for left, right in pairwise(cuts)
        ]
        result.append(dict(zip(labels, values)))
    return result


def field_values(fields):
    """값은 기재 내용이다. 날짜를 사건일로, 요청을 승인으로 승격하지 않는다."""
    dates, amounts, states = [], [], {}
    for label, value in fields.items():
        dates.extend(
            {"field": label, "value": item}
            for item in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", value)
        )
        if re.search(r"상태|후속|처리|성격", label):
            states[label] = value
        for match in re.finditer(
            r"USD\s*(\d[\d,]*(?:\.\d+)?|미산출|미확정|미결)", value
        ):
            raw = match[1]
            number = raw.replace(",", "") if raw[0].isdigit() else None
            amounts.append(
                {
                    "field": label,
                    "currency": "USD",
                    "value": int(number) if number and "." not in number else number,
                    "raw": match[0],
                    "resolution": "reported" if raw[0].isdigit() else "unresolved",
                }
            )
        if "USD" in label and re.fullmatch(r"\d[\d,]*(?:\.\d+)?", value):
            number = value.replace(",", "")
            amounts.append(
                {
                    "field": label,
                    "currency": "USD",
                    "value": int(number) if "." not in number else number,
                    "raw": value,
                    "resolution": "reported",
                }
            )
        if (
            re.search(r"금액", label)
            and re.search(r"미결|미확정|미산출|공란", value)
            and not re.search(r"USD", value)
        ):
            amounts.append(
                {
                    "field": label,
                    "currency": None,
                    "value": None,
                    "raw": value,
                    "resolution": "unresolved",
                }
            )
    return dates, amounts, states


def cost_terms(fields):
    """계약 표의 비용별 무료일·누진 구간만 읽는다. 적용 여부·금액 계산은 하지 않는다."""
    if not fields:
        return {}
    cost = next(iter(fields.values()))
    if cost not in ("보관료", "반납지연료", "장비반납지연료"):
        return {}
    free = next((v for k, v in fields.items() if "무료" in k), "")
    match = re.search(r"(\d+)일", compact(free))
    if not match:
        return {}
    first = " ".join(v for k, v in fields.items() if "첫" in k)
    following = " ".join(v for k, v in fields.items() if "다음" in k)
    range_match = re.search(r"1[~～](\d+)일", compact(first))
    rate = re.search(r"(?:일|:)(\d+)(?:달러|/일)?$", compact(first))
    last = re.search(
        r"(\d+)일(?:째)?부터:?(?:일)?(\d+)(?:달러|/일)?", compact(following)
    )
    if not all((range_match, rate, last)):
        return {}
    end, start = int(range_match[1]), int(last[1])
    if start != end + 1:
        return {}
    return {
        "cost": cost,
        "base_free_days": int(match[1]),
        "rate_tiers": [
            {"paid_day_from": 1, "paid_day_to": end, "daily_rate": int(rate[1])},
            {"paid_day_from": start, "paid_day_to": None, "daily_rate": int(last[2])},
        ],
        "application": "source_contract_row_only",
    }
