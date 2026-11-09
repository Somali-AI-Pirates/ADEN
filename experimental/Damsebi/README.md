# Damsebi 평가 실행 계획

[평가 가이드의 팀 공식 실행](../../docs/guides/c-module-guide.md#팀-공식-실행) 1단계 기록이다. 계획이 바뀌면 이 파일을 고친다.

- 작성: 2026-11-09
- 담당: Damsebi
- 목적: RAGAS Faithfulness와 Judge를 실제 모델로 검증해 고정하고, 채점 기준을 평가하면서 다듬는다. 기준 변경 이력은 [채점 기준 이력](../../docs/guides/judge-rubric.md)에 남긴다.

## 실행 조건

| 항목 | 내용 |
| --- | --- |
| 서비스 설정 | `configs/basic.yaml` (`retrieval.k`=8), 어댑터 `app.service.bootstrap:query_adapter` |
| 검색 방식 | naive, hybrid, hybrid_rerank |
| Judge | `gpt-6-luna`, 기준은 `judge.txt` 현재 버전(v2) |
| Faithfulness | RAGAS Faithfulness, 평가 모델 `gpt-6-luna` |
| 가리기 | Judge 입력에 검색 방식 이름을 넣지 않고 답변 하나씩 따로 채점한다(현재 코드 동작) |
| 질문 | 1문항 점검 후 개발용·확인용으로 나눈다. 확인용은 기준·프롬프트 조정에 쓰지 않는다 |

생성 모델과 평가 모델이 같아 자기 답변을 후하게 볼 수 있다. 세 방식에 같은 조건이라 방식 간 비교는 유지되지만 절대 점수는 높게 나올 수 있으므로 결과에 함께 적는다.

## 예상 호출 수

질문 N개 기준이다. 단가는 확인하지 않았으며 실제 토큰은 결과의 `summary.json`(서비스·`grading`)으로 확인한다.

| 단계 | 호출 |
| --- | --- |
| 서비스 실행 | 3N 요청, 요청당 생성 호출 최대 2회(`generation_max_attempts`) |
| Judge | 3N |
| RAGAS Faithfulness | 사실 주장이 있는 답변마다 주장 추출·검증 호출 |
| 기준 버전별 재채점 | Judge 3N (답변 재생성 없음) |

## 한도와 중단 기준

- 비용 한도: 두지 않는다(2026-11-09 팀 합의).
- 중단: 1문항 점검에서 Judge 파싱 실패나 RAGAS 평가 불가가 나오면 원인을 확인한 뒤 진행한다. 평가 호출이 연속 3회 실패하면 멈추고 부분 결과를 확인한다.

## 결과 위치

- 전체 기록: `artifacts/experiments/<실험 ID>/` (Git에 올리지 않음)
- 공식 비교·제출: `reports/<실험 ID>/`
- 기준 변경과 점수 변화: [채점 기준 이력](../../docs/guides/judge-rubric.md)

## 진행 상황

- [x] 재채점 기능 (#34)
- [x] 채점 기준 v2와 이력 문서 (#35)
- [x] RAGAS 설치·버전 고정과 연결 확인 (2026-11-09)
  - `eval` 추가 설치: ragas 0.4.3, langchain-community 0.4.1(0.4.2는 ragas가 쓰는 모듈 제거)
  - `gpt-6-luna` 실제 호출: 주장 2개 중 1개만 문맥과 맞는 답변 0.5, 모두 맞는 답변 1.0. 각 5회 반복 시 같은 값, 호출당 약 5초
  - RAGAS는 토큰 사용량을 반환하지 않아 Faithfulness 평가 토큰은 `null`로 남는다
- [ ] 1문항 점검: 형식·파싱·Langfuse·시간·토큰, 같은 기록 2~3회 재채점으로 점수 변동 확인
- [ ] 개발용 질문 세 방식 실행, v1·v2 재채점으로 점수 변화 표 첫 줄 기록
- [ ] 기준 확정 후 전체 재채점, 확인용 질문 확인, 대표 사례 사람 검수

## 확인 필요

- 공식 Basic 질문·정답 파일 위치(저장소에는 `data/basic/evaluation/custom.json`만 있음)
- 공유 색인 준비 상태, OpenAI·Langfuse 키
