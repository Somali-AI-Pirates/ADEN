"""확인한 원문 문장의 일치 조건. 규칙의 효력은 연결된 출처 맥락에 한정한다."""

import re

from app.ingestion.layout import compact, readable

# 공통 문구와 계약별 값이 섞인 문장은 여기서 일반화하지 않는다.
READING_RULES = (
    "업무 기록이며 각 원문 번호의 대상과 확인 시점을 함께 대조한다.",
    "업무 원문을 기록번호 단위로 묶었다. 후속 회신은 해당 업무 기록번호로 연결하며 행의 배열은 날짜순을 의미하지 않는다.",
    "업무 원문을 기록번호 단위로 묶었다. 동일 변경안의 후속 회신은 원 변경안 번호로 연결한다.",
    "모든 일자는 해당 터미널의 현지 날짜다. 장비관리번호는 내부 식별자다.",
    "표와 본문은 대조번호별 기재 상태다. 최초 접수일은 상태 확인 시각이 아니며, 상태 간 선후·정정 관계는 이 자료에 명시되지 않았다. 본문의 ‘현재’·‘완료’도 해당 기록의 표기이며 자료집 편집일의 현황은 아니다. 같은 대상의 기록이 상충하면 현재 상태를 확정하지 않고 담당자의 최신 접수 회신을 확인한다.",
    "이 절의 월 구분은 통지 기록일 기준이며, 연결 청구의 실제 양하월과 구분한다.",
    "총액은 해당 장비의 유료일수와 유효 감액을 반영한 값이며 일요율만으로 역산하지 않는다. 이 단위 대조표는 발행·입금 상태를 확인하는 원문이 아니다.",
    "이 표는 대상 식별용이다. 요청값·승인값·효력은 본문에 적힌 해당 원문 상태로 확인한다.",
    "표 수신·안내 확인은 1월 원판 조건에 관한 기록이다. 접수일이나 청구일을 실제 양하일로 해석하지 않는다.",
)
BUSINESS_RULES = (
    "유료일의 순번은 무료기간이 끝난 뒤 1부터 시작한다. 높은 구간 요율을 낮은 구간에 소급하지 않는다.",
    "실제 사건 원본의 장비번호와 날짜가 일치해야 비용 기간에 사용할 수 있다. 예약 예정일이나 전산 등록일을 실제 일자로 치환하지 않는다. 보관료의 조건 변경과 반납지연료의 조건 변경은 비용별로 따로 대조한다.",
    "계약 약정의 산입 시작·종료 방법을 적용하기 전에 실제 사건 원본과 조회 시각을 확인한다. 날짜가 같아도 예약 변경, 접수 처리, 현장 인수는 서로 다른 상태다. 누락 서류는 현장 담당자에게 재확인 요청하고 예정 정보를 확정란에 복사하지 않는다.",
    "원문에 적힌 서명일과 효력 조건을 함께 적용한다. 늦게 받은 회신이라는 이유만으로 최초 계약을 모두 폐기하지 않으며, 지정되지 않은 비용·장비·기간은 기존 조건을 계속 사용한다.",
    "정액 감액은 요율이나 유료일의 변경이 아니고 무료기간 연장은 산입 대상 중 무료일의 범위를 바꾸는 조건이다. 면제 요청과 승인 완료도 구분한다. 회신에 적힌 조건이 충족되지 않았으면 담당자의 기대나 구두 확인만으로 효력 발생을 기록하지 않는다.",
    "아래 구간별 산식은 실제 사건과 조회 기준에 유효한 승인 내역을 대조한 청구 기록이다. 상한은 구간 금액을 합한 뒤 해당 비용에만 적용한다. 감액을 적용한 확정액에서 같은 감액을 한 번 더 빼지 않는다. 수납은 별도 회계 원장에 기록하고 청구 총액 자체를 덮어쓰지 않는다.",
    "반납 증빙이나 승인 원본이 미결이면 담당 업무와 필요한 문서를 함께 인계한다. 현재 자료에 없다는 이유만으로 현장 작업이 실패했다고 기록하지 않는다. 기록의 해석 범위를 벗어난 상태는 해당 원본 수신 후 갱신한다. 최종 정정본이 있더라도 유지되는 비용을 소급 취소하지 않는다.",
    "계약 조건의 변경 권한은 한결루트해운 계약운영책임자에게 있다. 현장 접수자와 화주 연락담당의 요청은 승인 서명을 대신하지 않는다.",
    "두 비용은 같은 예약에서 발생하더라도 별도 산입 사건과 조건을 쓴다. 정정 승인으로 바뀐 항목만 청구에 반영하고 유지 항목을 다시 감액하지 않는다.",
    "이 약정은 최초 계약의 산입 방법과 요율을 보관한 원본이다. 이후 유효한 부분 변경이 있으면 지정된 항목만 교체하고 나머지 조항은 유지한다.",
    "현장 확인은 장비번호와 사건 유형을 기준으로 묶었다. 선하증권 초안 승인이나 예약 접수만으로 실제 반출·반납을 완료 처리하지 않는다.",
    "기간은 이용 터미널의 현지 달력 날짜로 세며 토요일·일요일·공휴일을 포함한다. 보관료는 실제 양하일을 제외하고 실제 적입 장비 반출일까지 포함한다. 반납지연료는 실제 적입 장비 반출일을 제외하고 지정 장치장 실제 공장비 반납일까지 포함한다. 두 비용의 기산 사건과 무료기간은 서로 독립적이다.",
    "단가는 장비 한 대당 USD이다. 각 비용의 무료기간이 끝난 다음 날을 유료일 1로 세고 첫 구간과 그 다음 구간의 일수에 해당 단가를 나누어 곱한다. 최고 단가를 전체 유료일에 소급하지 않는다. 세금·환율·해상운임과 그 밖의 비용은 이 계약표의 계산 범위에 포함하지 않는다.",
    "조건 변경은 계약운영책임자의 서명, 대상 예약·장비, 비용 항목, 적용기간이 확인되는 승인 기록에 따른다. 고객의 신청·견적·일정 통보를 승인으로 옮기지 않는다. 실제 사건과 승인 자료의 식별이 맞지 않으면 해당 변경을 편입하기 전에 원본을 확인한다.",
)
EXACT = {compact(text): (kind, text) for kind, texts in
         (("reading_rule", READING_RULES), ("business_rule", BUSINESS_RULES)) for text in texts}
PREFIXES = (
    "대상 장비는 20GP와 40GP다.",
    "예약을 신청할 때에는 화주명",
    "이미 접수된 VGM의 정정은 기존 접수 경로를 따라 요청한다.",
    "반입 준비에는 확정 예약번호",
    "이 문서는 선사 가온루트해운의 공식 변경 공지다.",
    "이 자료집의 2026년 상담 기록은 당시 예약",
    "공식 변경 공지 T-2702:",
    "고객의 감액·면제 요청이나 이의 접수는 승인과 다르다.",
    "LA 내부 보관료와 부산 반입 지연료",
    "내부 보관료와 별도 비용 가온루트해운의 이 자료집은",
    "S2 표의 대상 항목 이 자료의 요율 행은",
    "개별 무료기간 계약의 판본은 해당 예약의 최초 확정일",
    "청구 계산 초안, 발행 요청, 발행 완료, 수정·대체 발행은 별개 상태다.",
    "가온루트해운의 이 자료집은 2027-01-01 이후 실제 양하한",
    "이 자료의 요율 행은 LA S2 터미널 일반 건화물",
)


def match_common_rule(text):
    key = compact(text)
    if key in EXACT:
        kind, canonical = EXACT[key]
        return {"kind": kind, "text": canonical, "parameters": {}}
    if any(key.startswith(compact(prefix)) for prefix in PREFIXES):
        parameters = {}
        rate = re.search(r"실제양하일이(\d{4}-\d{2}-\d{2})이후인(N1|S2)일반건화물(20GP|40GP)의일요율을USD(\d+)에서USD(\d+)/장비/일로변경한다", key)
        deadline = re.search(r"예정출항일이(\d{4})년(\d+)월(\d+)일이후인예약은예정출항일이틀전낮(\d+)시까지SI를제출한다", key)
        if rate:
            parameters = {"date_basis": "actual_discharge_date", "valid_from_inclusive": rate[1],
                          "terminal": rate[2], "equipment_type": rate[3], "currency": "USD",
                          "old_daily_rate": int(rate[4]), "new_daily_rate": int(rate[5]), "unit": "per_equipment_per_day"}
        elif deadline and "한국표준시" in key and "평일만세는방식이아니다" in key:
            parameters = {"date_basis": "scheduled_departure_date",
                          "valid_from_inclusive": f"{int(deadline[1]):04d}-{int(deadline[2]):02d}-{int(deadline[3]):02d}",
                          "offset_days": -2, "day_basis": "calendar", "time": f"{int(deadline[4]):02d}:00", "timezone": "Asia/Seoul"}
        return {"kind": "business_rule", "text": readable(text), "parameters": parameters}
    if not key.startswith("보관료는실제양하일") or "장비반납지연료는실제반출일로부터" not in key:
        return None
    start = 0 if "양하일을포함해그날부터" in key else 1 if "양하일을제외하고다음날부터" in key else None
    offset = re.search(r"장비반납지연료는실제반출일로부터(\d+)일뒤", key)
    if start is None or not offset or "반출일까지포함한다" not in key:
        return None
    weekdays = "월요일부터금요일만세고주말은제외한다" in key
    calendar = "토요일·일요일을포함한달력날짜를센다" in key
    included = "반납일을포함한다" in key
    excluded = "반납일을제외한다" in key
    if weekdays == calendar or included == excluded or "반납지연료는달력일기준이다" not in key:
        return None
    return {"kind": "period_rule", "text": readable(text), "parameters": {
        "storage": {"start_event": "actual_discharge", "start_offset_days": start,
                    "end_event": "actual_loaded_gate_out", "end_inclusive": True,
                    "day_basis": "weekdays" if weekdays else "calendar",
                    "exclude_public_holidays": False},
        "return_delay": {"start_event": "actual_loaded_gate_out", "start_offset_days": int(offset[1]),
                         "end_event": "actual_empty_return", "end_inclusive": included,
                         "day_basis": "calendar"},
    }}
