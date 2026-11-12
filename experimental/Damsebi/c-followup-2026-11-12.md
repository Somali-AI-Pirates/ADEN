# C 후속: c4 판정 검수, B 수정 재평가 준비, HTTP 채점 연결 (2026-11-12)

기준은 main `5e36df8`(PR #52 병합)이다. AS-DEV19 재현 자료는 B에게 전달했고, **B 수정본과 수정 후 답변은 아직 받지 않았다.** 이 문서의 재평가는 준비까지이며 수정 후 재평가와 수정 후 HTTP 통합 검증은 하지 않았다.

## 요약

- **c4 불일치 원인**: 입력 문제가 아니다. Judge v3 조건 항목의 두 규칙이 "확인할 수 없는 계약 조건을 확인하지도, 확인 불가라고 밝히지도 않은 답변"에서 서로 다른 결론을 내는 기준 경계이고, 이것이 회차마다 다른 규칙 선택(반복 채점 변동)으로 나타났다. v3 확정으로 5회 채점한 c4는 조건 0이 4회(사유 세 갈래), 같은 M19의 c4b는 조건 1:0이 3:2였다.
- **판정 변동 처리 제안**: B 재평가 동안 기준을 고정하고, 비교하는 모든 답변을 3회 채점해 항목별 다수결과 일치 수를 함께 보고한다. 갈린 항목과 규칙 밖 사유는 사람 검수 목록에 올리되 점수를 덮어쓰지 않는다. 기준 보완(v4)은 B 재평가와 분리해 결정한다.
- **HTTP 채점 연결**: `http_records`가 HTTP 응답과 같은 request_id의 서버 trace로 RunRecord를 만들고, `regrade`가 그 답변을 다시 생성하지 않고 채점한다. 수정 전 HTTP 6건을 모두 연결했고, 1회차 2건을 실제 Judge·RAGAS로 채점했다.
- **수정 전 기준선**: 입력·답변·문맥·점수·설정 29개 파일의 해시를 [`bfix-baseline-20261112.sha256`](bfix-baseline-20261112.sha256)으로 고정했다.
- **평가 세트**: 개발·조정용과 최종 확인용을 아래처럼 나누자고 제안한다. 최종 확인용을 무엇으로 할지는 결정이 필요하다.
- **비교 도구**: [`fix_compare.py`](fix_compare.py)가 수정 전후를 같은 항목(Judge 항목별·Faithfulness·출처·상태·오류·재시도·시간·토큰·해당 없음·평가 불가·누락)으로 나란히 보여 준다.

## 1. c4 Judge 판정 불일치 검수

### 입력 대조

| 항목 | 내용 |
| --- | --- |
| 질문 | M19: 예약 HG2030-02713의 두 비용을 "실제 사건·계약·승인 회신으로 검산", 수납 충당·미수 계산, 근거 PDF·쪽 표기 |
| 참고 정답 | 양하 03-12·반출 03-21·반납 03-28. 보관료는 양하 다음 날~반출일 월~금 7일(무료 2·유료 5) USD 265, 반납지연료는 반출 다음 날~반납 전날 6일(무료 1·유료 5) USD 165, 수납 충당·잔액. 근거 AS095~AS099 p.149 |
| 저장 답변 | chsopo89 2026-11-09 실행(`local-rules2-adv-r1`, PR #49 이전 생성기). 금액·수납 충당·잔액이 맞고 실제/예정 반납일을 구분했다. 산입·무료·유료 일수와 산식은 "기록되어 있습니다"로 청구 명세 값을 옮겼다. **실제 반출일 03-21을 쓰지 않았고 "검산 미완료"를 밝히지 않았다.** reason은 "계약 조건 대조 회신 … 서로 대조했습니다" |
| 실제 사용 문맥 | 원본 기록 그대로(`ca72766b…`, 사례 세트 `context_changed=false`). AS099 수납, AS098 청구 명세(일수·산식), AS097 계약 대조 회신(추가 변경 없음·최초 계약 적용), AS096 사건(양하·반출·반납)과 다른 예약 AS043·AS005·AS057·AS009. **산입 규칙·무료일수·요율 원문인 비용 조건 약정(AS095 p.149)이 없다** |
| 채점 조건 | Judge v3 확정 `5c29900b`, `judge-output-v2`, `gpt-6-luna`. 기존 2회(`7071254`, 당시 미커밋 `judge.txt`의 해시가 지금과 같음)와 추가 3회(`5e36df8`). `graders.py`는 `7071254` 이후 바뀌지 않아 다섯 회차의 Judge 입력이 같다 |

입력·문맥 손상이나 정답 오류는 찾지 못했다. 사실 0의 공통 사유(문맥 S7에 있는 반출일을 빠뜨림)는 참고 정답과 실제 문맥을 구분한 판정이다. 2회차 사실 사유의 "산입 기간 날짜 범위 미제시"는 문맥에 없는 AS095에서 나오는 내용인데 그 구분을 적지 않았다. 반출일 누락만으로 0이므로 점수에는 영향이 없다.

### 판정 이유 대조 (v3 확정 5회)

칸은 사실·조건·근거·판단. 검산 진단은 5회 모두 incomplete다. 추가 3회는 원본 기록에서 c4·c4b 두 줄만 바이트 그대로 옮긴 `judge-cases-20261111-c4set`을 `regrade`했다(Judge 6회 호출, 게시 없음).

| 회차 | c4 (hybrid) | c4 조건 사유 요지 | c4b (hybrid_rerank) | c4b 조건 사유 요지 |
| --- | --- | --- | --- | --- |
| r1 (11-11) | 0·0·0·0 | 계약 산입 기준·요율 근거 없음 + 검산 완료처럼 제시 | 0·1·1·0 | 대상·기준 시각·실제/예정 구분 맞음 |
| r2 (11-11) | 0·0·0·0 | 반출일 누락 + 원계약 조건 미확인 + 검산 완료로 제시 | 1·1·0·0 | 같음 |
| r3 (11-12) | 0·0·0·0 | 반출일 누락·산입 기간 미연결 + 계약 규칙 없이 적용 확인 단정 | 0·0·0·0 | 확인 불가를 밝히지 않고 그 조건으로 검산한 것처럼 답함 |
| r4 (11-12) | 0·0·1·0 | 반출일 누락만 | 0·1·0·0 | 대상·기준 시각·단위 맞음 |
| r5 (11-12) | 0·1·0·0 | 대상·기준 시각·단위·실제/예정 구분·변경 없음 모두 맞음 | 0·0·0·0 | 원계약 조건 근거 없이 검산했다고 단정, 확인 불가 미표기 |
| 다수결(5회) | 0·0·0·0 | 조건 0 4/5, 근거 0 4/5 | 0·1·0·0 | 조건 1 3/5, 사실 0 4/5, 근거 0 4/5 |

### 원인 분류

1. **입력 문제: 아니다.** 위 대조와 같다.
2. **평가 기준 문제: 그렇다.** v3 조건 규칙의 두 문장이 이 답변 유형에서 충돌한다.
   - (가) "필요한 조건 확인을 빠뜨렸으면 0"과 "확인할 수 없는 조건을 확인 불가로 정확히 밝혔다면 빠뜨린 것으로 보지 않는다"를 함께 읽으면, 밝히지 않은 답변은 확인을 빠뜨린 것이라 조건 0이다(c4 r1·r3, c4b r3·r5).
   - (나) "검산을 완료했는지와 검산 근거가 충분한지는 condition이 아니라 검산 진단과 evidence·decision에서 판단한다"를 따르면 조건에서는 보지 않는다(c4 r5, c4b r1·r2·r4).
   - 별도로 실제 반출일 누락을 사실과 조건(시점)에서 함께 감점할지 정하지 않았다(c4 r2·r3·r4). v2부터 남은 [열린 결정](../../docs/guides/judge-rubric.md#열린-결정) "답하지 못한 항목의 귀속"과 같은 문제다.
   - 2026-11-11 기록은 c4 r1 사유를 "v3 규칙과 맞지 않는 남은 불일치"로 적었다. 그러나 그 사유의 앞부분(계약 산입 기준 확인 부족)은 (가)에 맞는다. Judge가 규칙을 어겼다기보다 규칙끼리 충돌하는 경계 사례로 보는 것이 정확하다.
3. **반복 채점 변동: 그렇다(2의 결과로).** 같은 입력에서 회차마다 (가)·(나)·반출일 귀속 중 다른 규칙을 골랐다. 사실(c4)·판단·검산 진단 상태는 5회 모두 같았다.

### 기존 기록 전체의 반복 변동 (같은 기준 2회, 모델 호출 없이 집계)

| 세트 | v2+새 스키마 | v3 초안 | v3 수정 1 | v3 확정 |
| --- | --- | --- | --- | --- |
| 저장 답변 사례 9건(36칸) | 3칸 | 2칸 | 5칸 | 3칸 |
| 검증용 가상 사례 5건(20칸) | 1칸 | 0칸 | 0칸 | 1칸 |
| 검산 진단 상태(14건) | 0 | 1 | 0 | 0 |

- v3 확정에서 갈린 칸은 c2 판단, c4b 사실·근거, 가상 3b 사실이다. 모두 규칙 경계(근거 없는 검산 완료 단정 여부, 청구 명세의 산입 일수가 필수 사실인지, 다른 기간 규칙의 사실 귀속)에 있다.
- 참고로 Basic v2 반복 채점(`basic-dev-20261110`, 96칸)은 7~12칸이 갈렸다([채점 기준 이력](../../docs/guides/judge-rubric.md#버전별-점수-변화)).

### 판정 변동 처리 제안 (사용자·팀 결정 필요)

1. **기준 고정**: B 재평가 동안 Judge 프롬프트(v3 확정 `5c29900b`)·스키마·Judge 모델·RAGAS 설정을 바꾸지 않는다. c4 경계를 고치는 v4는 B 재평가와 분리해 결정하고, v4를 쓰면 수정 전후 답변을 모두 v4로 다시 채점한다.
2. **반복 채점**: 비교에 쓰는 모든 답변(수정 전·후)을 같은 조건으로 3회 채점하고 항목별 다수결을 대표 점수로 쓴다. 회차는 별도 실험 ID(`…-e1`~`-e3`)로 저장하고 덮어쓰지 않는다. 결과에는 다수결 값과 일치 수(3/3, 2/3)를 함께 적는다(`fix_compare.py`가 `ID1+ID2+ID3`로 묶어 계산).
3. **사람 검수 대상**: (a) 일치 수가 2/3 이하인 항목, (b) 0점 사유가 그 항목의 규칙 밖 근거만 드는 경우(예: 조건 사유가 "검산 완료처럼 제시"뿐), (c) 수정 전후로 다수결이 바뀐 항목, (d) 평가 불가. 검수자는 질문·참고 정답·답변·실제 문맥·회차별 사유를 보고 항목별 판정·근거·검수자·날짜를 별도 표에 남긴다.
4. **최종 판정 선정**: 보고 수치는 Judge 3회 다수결이다. 사람 검수 결과는 별도 열로 함께 보고하고, 다수결과 다르면 그 문항에 표시한다. 유리한 회차만 고르거나 사람 검수로 Judge 점수를 덮어쓰지 않는다. 사람 판정을 공식 수치에 반영할지는 팀이 정한다.
5. **다수결의 한계**: c4b 조건은 5회 중 1이 3회인데, 3~5회차만의 다수결은 0(2/3)이었다. 규칙 경계 항목은 3회 다수결도 회차 묶음에 따라 뒤집힌다. 이런 항목은 수치 차이로 해석하지 않고 사람 검수와 기준 보완으로 다룬다.

기준 보완 방향(제안, v4 결정 때 개발·조정용 문항으로만 확인): 조건 규칙에 "청구 명세에 기록된 값을 청구서 값으로 옮긴 것은 추정이 아니다. 확인할 수 없는 계약 조건을 확인 불가로 밝히지 않은 것은 근거·판단·검산 진단에서만 감점한다"를 넣어 (나)로 통일하는 쪽을 권한다. v3의 의도(검산 완료 여부를 조건에서 보지 않음)와 맞고 같은 결함을 세 항목에서 중복 감점하지 않는다. 반대로 (가)로 통일하려면 "확인 불가 미표기는 조건 0"을 명시한다. 반출일 같은 누락 사실의 조건 귀속도 함께 정한다.

## 2. 수정 전 기준선 보존

| 구분 | 실험·파일 | 조건 |
| --- | --- | --- |
| 고정 근거 입력 | `artifacts/bc-fixed-evidence-20261111/` sufficient `2adf1961…`, insufficient `878579b5…`, 질문 `a01e803b…`, 정답 `3c86d570…` | 검색 성능 비교가 아니라 B/C 통합 검증용 |
| CLI 생성·채점 | `bc-fixed-insufficient-20261111`, `bc-fixed-sufficient-20261111` | 생성 `793e86b`, 생성 프롬프트 `c2ec0c3b…`, 설정 `configs/advanced.yaml` `0221d3af…`, Judge v3 `5c29900b`·`judge-output-v2`·`gpt-6-luna`, RAGAS 0.4.3 `gpt-6-luna` 출력 8,192. 조건별 1회 생성 |
| HTTP 수정 전 | `http-bc-sufficient-r1~r3-20261111`(manifest 커밋 `793e86b`, 미커밋 변경 있음으로 기록), 서버 로그 `artifacts/bc-api-server-sufficient.log` | sufficient만, 2문항×3회 |
| HTTP 연결 기록(이번) | `http-bc-sufficient-r1~r3-20261111-records`, 채점 `http-bc-sufficient-r1-20261111-records-v3-e1` | 2·3회차는 연결만 했다. 수정 후 채점 때 같은 기준·회차 수로 함께 채점한다 |
| 재현 묶음 | [`repro/AS-DEV19-insufficient-20261111/`](repro/AS-DEV19-insufficient-20261111/README.md)(Git 추적) | B 전달본 |

- 해시 목록: 저장소 루트에서 `sha256sum -c experimental/Damsebi/bfix-baseline-20261112.sha256`. 작성 직후 29개 모두 통과했다. 추적 파일 `judge.txt`·`shipping_qa.txt`·`advanced.yaml`도 수정 전 값으로 넣었으므로, B 수정이 생성 프롬프트나 설정을 바꾸면 그 줄이 FAILED로 나온다(변경 감지용).
- 부족 조건의 수정 전 기준선은 CLI 1회 생성뿐이다. 수정 후 HTTP 3회와 생성 횟수가 다르므로, 수정 전 HTTP 기준선을 같은 방식으로 3회 만들지(생성 6회 호출)는 결정이 필요하다. 지금은 만들지 않았다.

## 3. HTTP 요청과 평가 기록 연결

### 확인한 현재 구조

- API 미들웨어가 요청마다 `uuid4().hex` request_id를 만들어 응답 본문, `X-Request-ID` 헤더, 운영 로그에 넣는다.
- 서비스는 같은 request_id로 trace ID(`create_trace_id(seed=request_id)`)를 만든다. trace에는 `rag-query`(질문·방식 → 상태·답변·reason·인용, metadata에 request_id·실행 설정 해시·생성 프롬프트 해시·서버 처리 시간), `retrieval`(후보), `final-context`(최종 청크·두 해시·모델 문맥·토큰 수·k), `generation-attempt-N`(모델 입출력·사용량)이 있다.
- API 경로는 `run_record`를 넘기지 않아 서버에 RunRecord 파일이 없다. `http_benchmark`는 응답과 trace ID만 저장했고 #52에서는 HTTP 답변을 채점하지 않았다.
- `regrade`는 `runs.jsonl`의 답변·최종 문맥을 그대로 채점하고 점수 행에 request_id를 남긴다. 채점 회차는 채점 실험 ID(폴더), manifest `regrade.source_experiment_id`, Langfuse 점수 metadata의 `experiment_id`·`evaluation_set`으로 구분된다. 점수 행의 `experiment_id`는 답변 원본 실험 ID다. 그래서 별도 `evaluation_run_id`를 만들지 않고 채점 실험 ID를 그 역할로 쓴다.

### 구현 (최소 변경)

| 파일 | 변경 |
| --- | --- |
| `app/observability/tracing.py` | `Tracer.observations(trace_id)`: trace 관측을 입출력·메타데이터·사용량까지 조회 |
| `app/evaluation/scripts/http_records.py` | HTTP 기록 + 같은 request_id의 서버 trace → RunRecord(`runs.jsonl`), 미연결 사유는 manifest |
| `tests/evaluation/test_http_records.py` | 연결·불일치 거부·실패 시도·HTTP→`regrade` 채점(모의 Judge) |
| `docs/guides/c-module-guide.md`, `docs/design/contracts.md` | HTTP 답변 채점 절차, 추가 필드 |

- API·서비스·생성 코드와 HTTP 응답 계약은 바꾸지 않았다. 서버에 기록 파일을 남기는 방식 대신 이미 요청 단위로 남는 trace를 썼다. 대신 Langfuse 키·보존 기간에 의존한다.
- 답변·상태·reason·인용·질문·검색 방식·request_id가 HTTP 응답과 trace에서 같을 때만 연결한다. 문맥 해시와 모델 문맥 해시는 RunRecord 정규화에서 다시 계산해 대조한다.
- 연결 고리: HTTP 응답 request_id → trace ID → `<HTTP ID>-records/runs.jsonl`(request_id, `http_*`) → `<HTTP ID>-records-<기준>-eN/scores.jsonl`(request_id). 수정 전후 요청은 각자 request_id를 유지하고 question_id·검색 방식·답변 생성 코드(HTTP manifest 커밋)·생성 프롬프트 해시·실행 설정 해시로 비교한다.

### 검증

- 단위 테스트 7개(모의 trace·모의 Judge, 모델 호출 없음).
- 실제 trace 연결: 수정 전 HTTP 6건이 모두 연결됐다. 같은 고정 근거의 CLI 기록(`bc-fixed-sufficient-20261111`)과 후보 순서, `final_context_hash`, `model_context_hash`, 문맥 토큰 수, 1차 모델 입력, 실행 설정 해시가 6건 모두 같았다.
- 실제 채점: 1회차 2건을 Judge v3·RAGAS로 채점했다(게시 없음). 두 건 모두 1·1·1·1, 검산 complete, Faithfulness 1.0으로 같은 근거의 CLI 답변 채점과 같았다. 서비스 시간 9,574·11,635 ms, HTTP 시간 9,587·11,646 ms.

## 4. B 수정 후 재평가 절차

### 원칙: 생성 변경과 평가 기준 변경을 섞지 않는다

| 비교 | 답변 | 평가 조건 | 보는 것 |
| --- | --- | --- | --- |
| 생성 변경 효과 | 수정 전 답변 vs 수정 후 답변 | 같음(Judge v3·스키마·Judge 모델·RAGAS·정답·k·채점 회차 수) | B 수정의 효과 |
| 평가 기준 변경 효과 | 같은 답변 | v3 vs v4 | 기준 변경의 효과(별도 비교) |

- 수정 후 답변의 문맥 해시가 같은 조건의 수정 전 값과 같아야 생성 비교로 쓴다(아래 0단계 표).
- 수정 전 답변도 수정 후와 같은 세션·같은 회차 수로 다시 채점한다. 2026-11-11 점수와 새 점수의 차이는 Judge 반복 변동의 참고값으로 따로 적는다.
- 평가 코드(`app/evaluation`)가 수정 전후 채점 사이에 바뀌지 않았는지 확인한다.

### B에게 받을 자료

1. 수정 커밋(또는 브랜치·PR)과 변경 파일 목록. 생성 프롬프트(`shipping_qa.txt`)·설정(`configs/advanced.yaml`) 변경 여부.
2. 무엇을 어떻게 고쳤는지와 B가 수정·확인에 쓴 질문 ID 목록(AS-DEV19 외에 쓴 문항이 있는지). 최종 확인용 구분에 필요하다.
3. B가 직접 수정 후 답변을 만들었다면 그 실행의 원본 기록:
   - `run_benchmark`로 만들었으면 `artifacts/experiments/<ID>/`의 `manifest.json`·`runs.jsonl`.
   - API로 만들었으면 `http_benchmark` 폴더(`http_runs.jsonl`·`manifest.json`)와, 서버 trace가 팀 Langfuse 프로젝트에 저장됐는지. trace가 없으면 실제 사용 문맥을 확인할 수 없다.
   - 입력이 재현 묶음 `input/evidence.jsonl` 그대로인지, 생성 횟수, 그리고 고르지 않은 모든 출력.
   - 답변 문장만 받으면 재평가에 쓰지 않는다(실제 사용 문맥·해시·request_id가 없다).
4. B 쪽 검사 결과(단위 테스트 등).

B가 커밋만 주면 C가 아래 순서로 직접 생성한다.

### 실행 순서 (B 수정본 수령 후)

0. 사전 확인
   - `sha256sum -c experimental/Damsebi/bfix-baseline-20261112.sha256`: 기준선 파일 변경 없음(생성 프롬프트·설정 줄은 B 수정에 따라 FAILED일 수 있음).
   - `git diff 5e36df8 <B 커밋> -- app/evaluation`이 비었는지, `judge.txt` 해시가 `5c29900b…`인지.
   - 같은 조건이면 같아야 하는 수정 전 문맥 해시:

     | 조건 | 문항 | final_context_hash | model_context_hash | 토큰 |
     | --- | --- | --- | --- | --- |
     | insufficient | AS-DEV19 | `4f88bef23ee8…` | `368a18f32dc3…` | 3,345 |
     | insufficient | M19 | `c1684bcce4ee…` | `a8f49b8e4405…` | 3,508 |
     | sufficient | AS-DEV19 | `2e71dd247e6c…` | `f4210a8c8e2f…` | 3,872 |
     | sufficient | M19 | `67d5f62658f4…` | `5e3f83058e69…` | 3,641 |

1. B 수정본을 별도 worktree에 체크아웃한다(`git -c core.longpaths=true worktree add <경로> <B 커밋>`). 서버와 `http_benchmark`는 그 worktree에서 실행한다. HTTP manifest의 커밋이 답변 생성 코드 버전이 된다.
2. insufficient 조건으로 서버를 띄운다(PowerShell 예).

   ```powershell
   $env:ADEN_CONFIG="configs/advanced.yaml"; $env:ADEN_EVIDENCE_PROVIDER="app.service.evidence:stored_evidence"
   $env:ADEN_EVIDENCE_RUNS="<main checkout>/artifacts/bc-fixed-evidence-20261111/insufficient.jsonl"; $env:ADEN_EVIDENCE_CONTEXT="1"
   python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8765 *> artifacts/bfix-api-insufficient.log
   ```

3. 3회 요청한다(`r1`~`r3`). `--artifacts-dir`는 기록을 모을 폴더(예: main checkout의 `artifacts/experiments`)로 준다.

   ```bash
   python -m app.evaluation.scripts.http_benchmark --base-url http://127.0.0.1:8765 \
       --questions <main>/artifacts/bc-fixed-evidence-20261111/questions.json --modes hybrid_rerank \
       --condition "B 수정 <커밋>, 고정 근거 insufficient" --verify-langfuse \
       --experiment-id http-bfix-insufficient-r1-<날짜> --artifacts-dir <main>/artifacts/experiments
   ```

4. sufficient 근거로 서버를 다시 띄워 같은 3회를 실행한다(회귀 확인).
5. 여기부터는 이 도구들이 있는 main checkout에서 실행한다. HTTP 기록마다 서버 trace와 연결한다. 미연결이 있으면 사유를 확인하고, 다시 연결할 수 없으면 누락으로 센다.

   ```bash
   python -m app.evaluation.scripts.http_records --source http-bfix-insufficient-r1-<날짜> \
       --questions artifacts/bc-fixed-evidence-20261111/questions.json --config configs/advanced.yaml
   ```

6. 연결 기록의 `final_context_hash`·`model_context_hash`를 0단계 표와 대조한다. 다르면 생성 비교에 쓰지 않는다.
7. 채점: 수정 후 연결 기록 6개와 수정 전 답변(`bc-fixed-insufficient-20261111`, `bc-fixed-sufficient-20261111`, `http-bc-sufficient-r1~r3-20261111-records`)을 같은 조건으로 3회씩 채점한다. `http-bc-sufficient-r1-20261111-records`는 `-v3-e1`이 이미 있으므로 `-e2`·`-e3`만 더한다.

   ```bash
   python -m app.evaluation.scripts.regrade --source <답변 원본 ID> \
       --references artifacts/bc-fixed-evidence-20261111/references.json \
       --judge-model gpt-6-luna --ragas-model gpt-6-luna --experiment-id <답변 원본 ID>-v3-e1
   ```

8. 비교표를 만든다. 같은 답변의 채점 회차는 `+`로 묶는다.

   ```bash
   python -m experimental.Damsebi.fix_compare --references artifacts/bc-fixed-evidence-20261111/references.json \
       --out artifacts/bc-tables/bfix-compare.md \
       before-ins=bc-fixed-insufficient-20261111-v3-e1+bc-fixed-insufficient-20261111-v3-e2+bc-fixed-insufficient-20261111-v3-e3 \
       after-ins-r1=http-bfix-insufficient-r1-<날짜>-records-v3-e1+…-v3-e2+…-v3-e3 …
   ```

9. 1절의 기준으로 사람 검수 대상을 고르고 검수 표를 남긴다. AS-DEV19 부족 조건의 기대 동작(두 비용 "검산 미완료"와 확인하지 못한 항목 표기, USD 205·145를 "청구서상 금액"으로 구분, 산입 경계 비추정)은 답변 원문으로 직접 확인한다. Judge 기준을 B 수정에 맞춰 바꾸지 않는다.
10. Langfuse 점수 게시는 최종 평가 세트 이름을 정한 뒤 한 세트로 한다. 회차별 채점은 서로 다른 세트로 남긴다.
11. 결과를 새 날짜 문서로 기록한 뒤에만 재평가 완료로 표시한다.

## 5. 평가 세트 운영 (개발·조정용과 최종 확인용)

### 확인한 기존 방침과 사용 이력

- Basic custom 40: 개발용 8문항(S01·S04·S09·S12·M01·M03·M07·M16)은 기준 조정에 쓰고, 확인용 32문항은 쓰지 않는다([README](README.md)).
- Advanced 제공 자료: development 30, additional 30, security_tasks 2. 사용자 규칙(2026-11-11): C 작업에서 development만 검증에 쓰고 additional은 조회·실행·조정에 쓰지 않는다. C는 이 규칙을 지켰다([#46 보고서](pr43-advanced-dev-2026-11-11.md)).
- C가 기준·진단 조정에 직접 쓴 문항: AS-DEV19(c1·c2, B 재현 자료), AS-DEV28(c6), AS-DEV01(c7), Advanced custom M19(c3~c5b, B/C 고정 근거). 대체 근거 사례를 찾느라 AS-DEV17·21·23·27·29의 원문·근거 구조를 봤다.
- development 30문항 전체의 방식별·문항별 결과가 #46 보고서로 공개됐다. B가 이후 생성 수정에 어떤 문항을 썼는지는 확인되지 않았다.
- A(u_10o0o)의 검색 실험 기록에 development+additional 60문항 실행 결과가 있다(`experimental/u_10o0o/records/advanced-fresh-existing-evaluation-20261111/report.md`). additional은 C에서 쓰지 않았지만 팀 전체에서 실행되지 않은 것은 아니다. 검색 조정에 썼는지는 확인하지 않았다.

### 구분 제안

| 구분 | 문항 | 쓰임 |
| --- | --- | --- |
| 개발·조정용 | Advanced development 30, Advanced custom 40(M19 포함), Basic 개발용 8, Judge 가상 사례(`SYN-`) | 생성 프롬프트·Judge 기준 조정, 사례 검토, B 수정 확인. B 재평가 대상 AS-DEV19·M19도 여기에 속한다 |
| 최종 확인용 | Advanced additional 30(권장), Basic 확인용 32 | 생성 프롬프트·Judge 기준·설정을 확정한 뒤 한 번만 실행한다. 그 전에는 조회·실행·조정하지 않는다(기존 규칙 유지). 결과를 보고 기준이나 프롬프트를 고치면 그 세트는 더 이상 최종 확인용이 아니라고 기록한다 |

- 결정 필요: 최종 확인용을 additional로 할지. additional은 A 검색 실험에서 실행된 기록이 있으므로 A가 검색 조정에 썼는지 확인해야 한다. additional을 쓰지 않으면 development 중 C가 조정·검토에 쓰지 않은 22문항(AS-DEV02~16, 18, 20, 22, 24~26, 30)이 대안이지만, 문항별 결과가 이미 공개돼 약한 확인 세트다.
- 조정 금지는 [채점 기준 이력](../../docs/guides/judge-rubric.md#바꾸는-방법)의 기준 변경 절차에도 적었다. B에게도 최종 확인용 문항을 생성 수정에 쓰지 않도록 요청한다.

## 6. 비교 항목

`fix_compare.py`의 요약·요청별 표가 아래 항목을 같은 정의로 보여 준다. 채점 실험마다 나오는 `regrade` 보고서(README·`comparison.csv`)도 같은 값을 쓴다.

| 항목 | 출처 | 집계 | 주의 |
| --- | --- | --- | --- |
| 조건 | 답변 원본·채점 manifest, trace | 답변 생성 커밋·미커밋, 생성 프롬프트 해시(HTTP 기록), 설정 파일 해시, Judge 프롬프트·스키마·모델, 정답 파일 해시, 문맥 해시 | 조건이 다르면 같은 표에서 비교하지 않는다 |
| Judge 사실·조건·근거·판단 | `scores.judge` | 1점 수/평가 대상, 해당 없음(NA)·평가 불가(UN)·동률(split) 건수. 반복 채점은 다수결과 일치 수 | 0점은 평균에 포함, NA·UN은 제외 |
| 검산 진단 | `calculation_checks.status` | 상태별 건수 | 공식 점수 아님 |
| RAGAS Faithfulness | `scores.faithfulness` | 평균(대상/NA/UN) | 부족·AS-DEV19의 틀린 산입 경계도 1.0이었다. 검산 판단은 Judge로 본다 |
| 출처 정확성 | Judge 근거(공식), 인용·최종 문맥·참고 근거 | 인용 중 최종 문맥에 있는 수 / 참고 근거 쪽과 같은 수 / 전체 | 참고 근거 쪽 일치는 진단이다. 대체 근거를 인용하면 낮게 나오므로 판정은 Judge 근거로 한다 |
| 상태·오류·재시도 | `runs.status`·`error_type`·`attempts`, HTTP `outcome` | 상태별 건수, 오류 수, 재시도한 요청 수 | 추가 확인·보류는 오류가 아니다 |
| 지연시간 | 서버 `latency_ms`(service), `http_latency_ms`(http_client) | 중앙값 | 채점·trace 조회 시간 제외, 범위가 다른 값은 섞지 않는다 |
| 토큰 | `usage` | 생성 입력/출력 합, 사용량 완전/부분/미확인 건수 | 평가 호출 토큰은 보고서 `summary.json`의 `grading` |
| 누락 | `http_records.unlinked`, 채점 없는 요청 | HTTP 미연결·채점 없음 건수 | 미연결 요청은 요청별 표에 사유와 함께 남는다 |

## 검증한 것

- 단위 테스트(새 테스트 7개 포함)와 Ruff. 결과는 PR 설명에 적는다.
- 기존 기록 분석: c4·c4b의 입력·문맥·판정 이유 대조, 같은 기준 반복 채점 변동 집계(모델 호출 없음).
- 실제 모델 호출: c4·c4b Judge v3 3회(6회 호출), 수정 전 HTTP 1회차 2건 Judge·RAGAS. 게시하지 않았다.
- 실제 trace 조회: 수정 전 HTTP 6건 연결과 CLI 기록 대조.
- `fix_compare.py`: 기존 수정 전 기록과 c4 반복 채점으로 표 생성, 다수결 동률·평가 불가 처리 확인.
- 기준선 해시 29개 `sha256sum -c` 통과.

## 미검증·대기

- **대기(B 수정본 필요)**: 수정 후 생성, 수정 후 HTTP 통합 검증, 수정 전후 재평가와 비교표, 사람 검수.
- 결정 필요: 반복 채점·사람 검수 방침 채택, v4 기준 보완 여부와 방향, 최종 확인용 세트, 부족 조건의 수정 전 HTTP 기준선 생성 여부.
- HTTP 연결은 Langfuse trace가 남아 있어야 한다. 키가 없거나 보존 기간이 지나면 연결할 수 없다.
- `http_records`는 서버 코드 버전을 직접 받지 않는다. HTTP manifest의 커밋을 쓰므로 서버와 클라이언트를 같은 checkout에서 실행해야 한다.
- 생성·Judge·RAGAS가 모두 `gpt-6-luna`라 자기 평가 경향이 있을 수 있다.
