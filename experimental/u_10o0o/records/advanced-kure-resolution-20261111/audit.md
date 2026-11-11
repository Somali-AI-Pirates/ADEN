# 실패 결과의 실제 근거 검수

독립/사람 검수 없이 원문을 자체 대조했다. 인용문과 질문은 로컬 artifacts에만 보관한다. 같은 문항의 각 조건에 허용 근거를 대칭 적용했다.

| 문항 | 판정 근거 | A | B | C | D | E |
|---|---|---|---|---|---|---|
| official_additional:AS-ADD01 | 같은 서비스 자료의 제목·일반 조건·제외 대상이 질문의 항로·화물·장비 범위를 모두 제공한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_additional:AS-ADD02 | SI 마감 안내와 장비 배정/예약 변경 조건은 별개다. 변경 요청 근거가 충분히 반환되지 않았다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| official_additional:AS-ADD03 | 취소 접수·미회신과 승인 대기를 완료와 구분하는 동일 업무 절차가 명시돼 있다. 개별 예약 상태를 전용하지 않는다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_additional:AS-ADD05 | 마감 개정 원문만 있고 수하인/착화 통지처 구분 근거가 없다. | original_pass | original_pass | original_pass | original_pass | not_supported |
| official_additional:AS-ADD06 | 같은 서비스의 장비별 VGM 및 SI 창구와 순중량/화물총중량 구분을 함께 확인한다. | not_supported | not_supported | alternative_supported | alternative_supported | alternative_supported |
| official_additional:AS-ADD07 | 예약 확정·차량 배차와 반입 허가의 차이 및 반입 회신 확인이 모두 명시되어 있다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_additional:AS-ADD09 | 서류·반입 정정과 VGM 경로가 있어도 변경 봉인번호의 운송사 통보까지 모두 확인되지 않아 실패로 유지한다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| official_additional:AS-ADD10 | 신규 접수와 이미 이메일로 접수한 기록의 정정을 분리하는 같은 개정 원문과 기존 기록 연결 사례를 함께 확인한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_additional:AS-ADD14 | 같은 사건의 유효 승인·부분 정산서가 무료/유료 날짜 구간과 산식·확정액을 직접 제시한다. 고객 회신의 무권한 주장은 승인 근거에서 제외한다. | original_pass | original_pass | alternative_supported | original_pass | alternative_supported |
| official_additional:AS-ADD19 | 같은 사건의 청구서 자체가 비용별 부담 주체와 금액·합계의 구분을 모두 제공한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_additional:AS-ADD21 | 같은 사건의 승인·청구서에서 반납료에만 적용되는 상한과 유지 보관료·합계를 확인한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_additional:AS-ADD23 | 같은 사건 계약·승인·청구서는 있지만 실제 반출일이 빠져 첫 산입일을 확인하지 못한다. | original_pass | not_supported | original_pass | original_pass | not_supported |
| official_additional:AS-ADD25 | 같은 사건의 승인 효력일과 조회 기준을 대조하고 청구서의 현재 유효 금액을 확인한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_additional:AS-ADD27 | 같은 사건의 승인 대조 원문에 조건 충족일이 있고 청구서에 반영 금액이 있어 별도 사건대장 없이 요청 사실이 충족된다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_additional:AS-ADD29 | 같은 사건의 적용 조건 충족을 확인한 회신과 청구서에서 개정 범위·유지 항목·금액을 확인한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_development:AS-DEV02 | 서류 마감만으로 예약 접수 창구·시점을 충족하지 못한다. 질문의 두 업무 중 예약 요청이 남는다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| official_development:AS-DEV04 | SI/VGM 정의 차이와 값이 달라도 오류가 아니라는 설명을 한 페이지가 모두 제공한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_development:AS-DEV05 | 마감은 있으나 순중량과 포장 포함·컨테이너 제외 화물총중량의 구분 근거가 빠졌다. | original_pass | original_pass | original_pass | not_supported | not_supported |
| official_development:AS-DEV06 | SI 창구·화물/컨테이너 중량 차이·신규 접수일에 따른 VGM 경로를 같은 서비스 원문에서 함께 확인한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_development:AS-DEV08 | 동일 운항 자료에 초안 승인과 적재 완료의 구분 및 대상 장비에 대한 운항팀 실제 회신 필요가 명시돼 있다. | alternative_supported | alternative_supported | original_pass | original_pass | original_pass |
| official_development:AS-DEV09 | 중량 변경·기존/새 값·단위·사유는 있으나 예약 식별·관련 첨부를 포함한 정정 절차 전체 근거가 충분하지 않다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| official_development:AS-DEV13 | 같은 사건 금액과 요율만으로 누락된 실제 양하일 원본을 대체하지 않았다. 요청한 무료/유료 날짜 구간의 실제일 검증이 부족하다. | original_pass | original_pass | not_supported | original_pass | original_pass |
| official_development:AS-DEV16 | 같은 사건의 정정 승인에 이전/수정 보관료와 유지 반납료가, 청구서에 합계가 있다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_development:AS-DEV20 | 검색된 유사 화주·화물의 다른 사건 및 일반 안내로 질문 사건의 실제 종료일·산입·금액을 확정할 수 없다. | original_pass | original_pass | original_pass | original_pass | not_supported |
| official_development:AS-DEV22 | 같은 사건의 승인과 청구서가 보관료 무료 연장 범위·유지 항목·두 비용 금액을 제공한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_development:AS-DEV24 | 같은 사건의 반납료 면제 범위와 유지 보관료·합계가 승인·청구 원문에 있다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_development:AS-DEV26 | 같은 사건의 정액 감액 승인 및 청구서가 적용 비용·한 번 차감하는 순서·확정 금액을 제공한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| official_development:AS-DEV30 | 같은 사건의 실제/예정 기록과 청구서가 미수신 반납 증빙·확정 보관료·미결 반납료와 합계를 구분한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| scenarios:AS-S04-02 | 두 사건 청구 금액은 있으나 두 번째 사건의 실제 반출일 원문이 없다. 청구 일수에서 실제일을 역추정하지 않았다. | original_pass | original_pass | original_pass | original_pass | not_supported |
| scenarios:AS-S04-05 | 두 사건 청구 금액은 있으나 두 번째 사건의 실제 반출일 원문이 없다. 다른 사건 날짜는 허용하지 않았다. | original_pass | original_pass | original_pass | original_pass | not_supported |
| scenarios:AS-S04-08 | 두 사건 청구 금액은 있으나 두 번째 사건의 실제 반출일 원문이 없다. 계약·승인 조건은 실적 기록을 대체하지 않는다. | original_pass | original_pass | original_pass | original_pass | not_supported |
| semantic_queries:AS-SEM-08 | 미승인 요청값으로 실제 완료 기록을 덮어쓰지 않는 원칙이 같은 정정 기록에 명시돼 있다. 예시의 날짜를 질문 대상 값으로 전용하지 않는다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| semantic_queries:AS-SEM-09 | 동일 배분 자료가 수신액·승인 배분·미배분을 구분하고 미승인 금액의 청구 차감을 금지한다. 다른 사건 금액은 전용하지 않는다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | original_pass |
| semantic_queries:AS-SEM-10 | 동일 VGM 자료집에 반복된 일반 상태 판정 문장만 허용한다. 개별 대상의 상태는 전용하지 않는다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| semantic_queries:AS-SEM-13 | 같은 LA 자료의 현지 날짜·시차 비보충 규칙이 질문의 모든 요청을 충족한다. | alternative_supported | alternative_supported | original_pass | original_pass | original_pass |
| semantic_queries:AS-SEM-14 | 같은 개정 관리 자료가 N1 20GP 유지 조건을 명시한다. 일반 기본표나 장비만 유사한 자료는 단독 근거로 승인하지 않는다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | not_supported |
| semantic_queries:AS-SEM-21 | 반입 운영 기록으로 예약지원팀의 확정 회신을 대체하지 못한다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| semantic_queries:AS-SEM-22 | SI 변경 안내만으로 장비 배정 및 예약 변경 완료를 판단하지 못한다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| semantic_queries:AS-SEM-23 | 일반 서비스/예약 자료만으로 서로 다른 요청 절차를 모두 충족하지 못한다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| semantic_queries:AS-SEM-24 | SI 정정 근거와 별도로 필요한 기존 VGM 접수 경로·기록 연결 근거가 남는다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| semantic_queries:AS-SEM-26 | 담당 연락처만으로 실제 적재 여부를 입증하지 못한다. 실제 회신 또는 계획/완료 구분 근거가 필요하다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| semantic_queries:AS-SEM-28 | 문의 창구·일반 인계만으로 도착 계획 변경을 실제 반영하는 절차를 모두 충족하지 못한다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| semantic_queries:AS-SEM-29 | 입금 수신과 특정 청구의 승인 배분을 구분하는 근거가 최종 문맥에서 빠진 조건은 실패다. | not_supported | not_supported | original_pass | original_pass | original_pass |
| semantic_queries:AS-SEM-30 | 요청/보완이 승인·비용 반영이 아님을 명시한 감액 업무 기록과 단계별 인계 원칙을 함께 인정한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | not_supported |
| semantic_queries:AS-SEM-31 | 마감·기존 VGM 정정·차량 배차와 반입 허가의 세 요구를 모두 충족해야 한다. 일부만 반환한 조건은 실패다. | not_supported | not_supported | not_supported | not_supported | not_supported |
| semantic_queries:AS-SEM-32 | 터미널·계약 식별 정보가 없는 질문에 상충하는 주말 산입 규칙이 존재해 별도 모호성으로 분리했다. | scope_ambiguous | scope_ambiguous | scope_ambiguous | scope_ambiguous | scope_ambiguous |
| semantic_queries:AS-SEM-33 | 질문이 요청한 두 날짜 선택 기준을 같은 계약 원문이 직접 구분한다. 최초 확정일은 유지하고 일요율은 실제 양하일 기준임을 명시한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| semantic_queries:AS-SEM-34 | 같은 개정 원문의 비대상 쪽에 S2 제외와 두 규격의 무료일수·일요율이 모두 명시돼 있다. | not_supported | not_supported | alternative_supported | alternative_supported | alternative_supported |
| semantic_queries:AS-SEM-35 | 개별 계약의 기본값 대체 원칙과 유료일 계산·고정액 한 번 차감·음수 0 제한을 함께 반환했을 때만 인정한다. | alternative_supported | alternative_supported | not_supported | not_supported | not_supported |
| semantic_queries:AS-SEM-36 | 추가 무료일의 대상 제한 승인과 금액 감액을 무료일 변경으로 보지 않는 계약 원칙을 함께 확인한다. | not_supported | not_supported | not_supported | alternative_supported | alternative_supported |
| semantic_queries:AS-SEM-37 | 대체 감액 합산 금지는 있으나 계산 초안과 현재 유효 발행 통지 구분이 빠졌다. | original_pass | original_pass | original_pass | original_pass | not_supported |
| semantic_queries:AS-SEM-38 | 예정/배차로 실제 반출 완료를 확정하지 않는 기록과 실제 종료일에 따른 기간 규칙을 함께 제공한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| semantic_queries:AS-SEM-39 | 과거 고객 추정이 공식 요율이 아님과 실제 양하월 기준을 적용하는 계산 기록을 함께 확인한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| semantic_queries:AS-SEM-40 | 같은 부산–LA 서비스의 냉동 별도 조건과 일반 자료 적용 기간을 함께 확인한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| work_rules:AS-WR-12 | 신규 접수일·장비별 포털 기준과 기존 접수의 중복 제출 금지·기록 연결 정정이 같은 개정 원문에 함께 있다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
| work_rules:AS-WR-19 | 대체 승인 합산 금지는 있으나 감액 적용 범위·음수 0 제한까지 모두 뒷받침하지 못한다. | original_pass | original_pass | not_supported | not_supported | not_supported |
| work_rules:AS-WR-20 | 고객 상담 메모·최근 내부 메모가 공식 요율 정정을 대체하지 않는다는 동일 개정 담당 회신을 인정한다. | alternative_supported | alternative_supported | alternative_supported | alternative_supported | alternative_supported |
