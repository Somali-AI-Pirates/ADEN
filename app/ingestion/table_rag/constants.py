"""Advanced corpus field and source-role mappings."""

import re

ROLE_MAP = {
    "비용 조건 약정": "contract",
    "계약 조건 확인": "contract",
    "현장 사건 수신대장": "events",
    "사건·증빙 접수 대조": "events",
    "계약 조건 대조 회신": "approval",
    "비용 청구 명세": "invoice",
    "수납 및 미결 인계": "receipt",
    "비용별 정산 확인": "settlement",
}

ROLE_ORDER = (
    "contract",
    "events",
    "approval",
    "invoice",
    "receipt",
    "settlement",
    "record",
)

HA_ROLES = {
    "계약 조건 확인": "contract",
    "사건·증빙 접수 대조": "events",
    "비용별 정산 확인": "settlement",
}

DATE_FIELDS = {
    "양하 원시기록": "actual_discharge_date",
    "적입 반출기록": "actual_gate_out_date",
    "반출 전표": "system_entry_date",
    "공장비 인수증": "actual_designated_return_date",
}

HG_EVENT_FIELDS = {
    "실제 양하": "actual_discharge_date",
    "실제 적입 장비 반출": "actual_gate_out_date",
    "실제 지정 장치장 반납": "actual_designated_return_date",
    "반납 예약 예정": "scheduled_return_date",
    "전산 입력일": "system_entry_date",
}

CODE = re.compile(
    r"(?<![A-Z0-9_])([A-Z][A-Z0-9]*(?:\s*[-‐‑–—−－]\s*[A-Z0-9]+)+)(?![A-Z0-9_])", re.I
)

IS_KEYS = {
    "SI기록",
    "VGM 대조",
    "검산번호",
    "검토 기록번호",
    "계산 메모",
    "계획번호",
    "기록번호",
    "단위 대조",
    "당사자 등록",
    "대조번호",
    "등록번호",
    "문서 기록",
    "반입 기록",
    "상태 기록",
    "수정통지",
    "신청·승인 기록",
    "업무 기록번호",
    "운항 문의",
    "인계번호",
    "입금 기록",
    "접수 대조",
    "접수번호",
    "처리 기록",
    "확인번호",
}

IS_ANNOTATED_KEYS = {"수정통지", "입금 기록"}
