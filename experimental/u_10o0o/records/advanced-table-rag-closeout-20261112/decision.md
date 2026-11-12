# 최종 선택: DB 우선 통합, 요청별 인용 검사 OFF

## 결정과 구현 계약

2026-11-12 사용자가 정확도·지연·기능 차이를 확인한 뒤 이 구성을 확정했다. 개인 Advanced 실험의 인계 기준이며 운영 제품의 자동 교체는 아니다.

```python
from experimental.u_10o0o.table_rag_unified.agent import (
    UnifiedAgent,
    integrated_runtime,
)

with integrated_runtime():
    result = UnifiedAgent(
        retriever,
        client,
        db_first=True,
        verify_evidence=False,
    ).run(question)
```

기존 CLI로 실행할 때에는 다음처럼 **--db-first를 명시하고 --verify-evidence를 붙이지 않는다.** 기존 결과를 덮어쓰지 않는 새 출력 경로가 필요하다. 명령은 실제 유료 API를 사용한다.

```bash
.venv/bin/python -m experimental.u_10o0o.table_rag_unified query   "조회할 질문" --db-first   --output artifacts/u_10o0o/새-db-first-결과.json
```

저수준 생성자와 기존 CLI의 비교용 기본값은 바꾸지 않았다. 위 명시적 설정이 최종 인계 계약이다. 재현에 필요한 로컬 원본·페이지/표 벡터·검증 SQLite·모델 캐시와 기존 API 자격증명은 Git에 포함하지 않는다. 준비 방법은 [통합 패키지](../../table_rag_unified/README.md)와 [표 저장소](../../table_rag/README.md)를 따른다.

## 유지하는 기능

| 항목 | 최종 설정·이유 |
|---|---|
| 공통 기록 색인 | HG·HA 사건과 식별 가능한 IS 기록의 코드·필드·역할·원본 연결을 읽기 전용 SQLite에서 조회 |
| 초기 DB 우선 | 질문의 완전 코드가 모두 유일한 대상과 원본에 연결되면 DB 근거부터 제공. 그렇지 않으면 전체 하이브리드 |
| 후속 도구 | Luna가 lookup_records, search_evidence, read_sources, query_tables, finish 중 필요한 도구 선택 |
| 문서 검색 | 원본 페이지+IS 표 내용, 전체 코드 보존 BM25·KURE-v3 벡터 5:5 RRF(k=60), 검색마다 KURE nano 1회 |
| 대상·범위 확인 | 원문/질문에 없는 코드 추측 금지, 예정/실제 날짜·단위·통화·모호성 구분, 필요하면 추가 확인으로 종료 |
| 선택 출처 보존 | 실제 관측한 선택 출처를 최종 후보와 문맥에 보존. 최종 문맥에서 누락되면 충분 상태를 그대로 유지하지 않음 |
| 예산 | 검색별 후보20·합친 후보20·최종 출처 최대8쪽·SQL 포함8,000토큰 |
| 모델 | 임베딩 KURE-v3, 리랭커 KURE-Reranker-nano, GPT-6 Luna medium. 추가 학습 없음 |
| 호출 | 최대5회 도구 작업+finish, 자동 API 재시도 없음. DB 우선도 Luna 호출은 있음 |

KURE-v3 revision은 `73d5e710bc0b1f324a60746922cbad6d38f480d4`, KURE nano는 `8514b9e113f3b27c441ed9c2b50f81d80388b2fd`로 실험 자산을 고정했다. 실제 사용 지문은 각 protocol.json이 기준이다. 질문/리랭커 입력은 2,048/2,048토큰이며 원문 보존 예산과 구분한다.

## 제외하거나 비교용으로 남긴 기능

- HG 전용 `complete_sources_with_dates` 보강은 통합 실행 범위에서 비활성화한다. HG를 조회하지 않는다는 뜻이 아니다. 공통 RecordIndex와 lookup_records가 HG·HA·IS 원본을 제공한다.
- `verify_evidence=True`의 요청별 인용 강제·미열람 후보 미리보기·인용 존재/보존 검사는 기본 인계에서 제외한다. 비교 옵션과 결과는 보존한다.
- 새 `table_rag_workflow`의 사전 요청 계획·근거 카드·제한된 보충 흐름은 채택하지 않는다.
- 최종 답변 생성, 새 학습, 새로운 모델 선정, 운영 API 전환은 종료 시점의 범위가 아니다.

공통 기록 조회는 Python이 정해진 파라미터 SQL로 대상을 찾는 작업이다. query_tables는 모델이 관측 스키마로 작성한 SELECT/WITH로 표를 필터·집계하는 작업이다. 도구 등록, 실제 호출, SQL 성공, 최종 근거 유지, 답변 정확도를 별개로 기록한다.

## 선택에 사용한 관측값

| 실행·구성 | 공식 실제 근거 | 카테고리 근거형 | 지연 p50/p95 | 해석 |
|---|---:|---:|---:|---|
| 과거 HG 보강 KURE-v3 B | 57/58 | 196/200 | 별도 실행별 기록 참조 | 통합 이전 채택 기록. 최종 OFF의 점수로 옮기지 않음 |
| DB 우선 비교의 B: 통합·검사 OFF | 56/58 | 193/200 | 전체 5.47/16.58초 | 명시 코드 109건의 검색 생략 이득을 확인 |
| 요청별 확인 비교의 A: 통합·검사 OFF | 56/58 | 미실행 | 공식 5.50/18.00초 | 바로 아래 ON과 같은 실행의 대조군 |
| 요청별 확인 비교의 B: 통합·검사 ON | 57/58 | 미실행 | 공식 9.42/25.71초 | +2/−1, 지연 증가. 동료 데이터 ON/OFF 검증 없음 |
| 마지막 묶음 비교의 A: 통합·검사 OFF | 53/58 | 188/200 | 전체 5.74/21.19초 | 새 실행·동시8요청. 이전 동시4요청과 속도 직접 비교 금지 |
| 마지막 묶음 비교의 B: 새 workflow | 46/58 | 158/200 | 전체 19.53/40.89초 | 정확도·지연 모두 불리하여 제외 |

[과거 채택](../advanced-table-rag-kure-v2-preservation-20261111/report.md) · [DB 우선](../advanced-table-rag-db-first-20261112/report.md) · [요청별 확인](../advanced-table-rag-request-evidence-20261112/report.md) · [마지막 묶음 비교](../advanced-table-rag-bounded-workflow-20261112/report.md)

이 표의 서로 다른 실행을 합산하거나 최고 점수만 모아 하나의 기준선 수치로 만들지 않는다. OFF의 56과53 차이는 새 LLM 도구·출처 선택과 검수 출력이 달라진 관측이며 반복 실행 분산을 추정한 결과는 아니다. 이 종료 작업에서 과거 57을53으로 정정하지 않는다.

ON의 효과는 공식 AS-DEV06·AS-ADD06 회복, AS-ADD09 하락이었다. 후보 원문을 더 읽으면 이미 찾은 근거를 살릴 수 있지만, 모델이 운송사 통지처럼 필요한 요구를 빠뜨리면 인용 존재 검사가 이를 보장해서 발견하지 못한다. API 호출84→130, p50 약71% 증가에 비해 추가 효과의 일반화가 확인되지 않아 OFF를 선택했다. 정확도 최고·속도 최고를 동시에 입증한 선택이라고 주장하지 않는다.

## 운영 구현자에게 전달할 내용

이 문서의 명시적 설정·원본 출처 계약을 이식한다. 실제 운영 구현에서는 준비/종료, 오류 상태, 출처·날짜 의미, 읽기 전용 SQL, 도구 예산을 별도로 통합 검증한다. 기존 동료 평가에서 좋아진 모호성 처리·출처 보존을 끄지 않는다. 요청별 인용 검사만 OFF다. 이 기록을 새 품질 벤치나 운영 승인으로 해석하지 않는다.
