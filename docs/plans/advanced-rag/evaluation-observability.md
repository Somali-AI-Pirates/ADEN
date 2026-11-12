# TableRAG 관측·평가 설계

> 2026-11-12 현재 구현 기준은 DB 우선 통합 TableRAG다. [현재 구현 기록](unified-implementation-log.md)과 [빠른 시작](../../guides/quickstart.md)을 따른다. 아래 A 이식 내용은 초기 설계 기록이다.

상태: 구현 계획. 현재 코드의 평가 기능은 참고하되, 새 TableRAG 경로와 LangChain callback 연결은 앞으로 구현한다.

**Langfuse는 실행과 점수의 연결, Ragas는 표준 품질 지표, 별도 Judge는 업무 판단의 채점**을 맡는다. 같은 평가를 세 곳에서 중복 호출하지 않는다. 사용자 요청 처리에는 검색·계획·답변만 실행하고, 품질 채점은 결과를 저장한 뒤 별도 작업으로 수행한다.

프로젝트 필수 기준은 [발제문](../../project-brief.md), 개인 실험 검수 절차는 [평가 정책](../../../experimental/u_10o0o/evaluation-policy.md)이다. 이 계획은 검색 근거가 충분하다는 점수와 실제 답변 정확도를 구분한다.

## 1. 전체 흐름과 정답 차단

```text
질문 manifest ──> 같은 RAG 실행 ──> 검색·SQL·최종 문맥·답변 저장
                                      │
                                      ├─> Langfuse 실행 trace
                                      │
                  저장 완료 후        v
참고답/허용근거 ───────────────────> 로컬 score + Ragas + 업무 Judge
                                      │
                                      └─> trace에 점수 연결 + 유형별 보고서
```

실행에 제공할 항목은 질문 문자열과 실행용 ID뿐이다. Langfuse DatasetItem 자체에도 질문·실행 ID·정답이 아닌 메타데이터만 넣는다. expectedOutput과 정답 metadata에 참고답·허용 근거를 업로드하지 않는다. gold는 scorer 전용 로컬 저장소에 두고 평가 로더만 ID로 조회한다. DatasetItem 전체를 RAG 함수에 넘기지 않는다.

모든 비교 조건의 검색·문맥·답변 저장을 마친 뒤 참고 근거를 읽는다. 실행 결과를 보고 선택기나 프롬프트를 고쳤다면 새 실행 버전으로 구분한다. 모델 검수도 gold를 참조할 수 있지만 검색 결과를 고치는 데 사용하지 않는다.

## 2. Langfuse trace의 형태

```text
table_rag.request [request_id, run_id, question_id, config/index/prompt hash]
  initial_search
    query_embedding / sparse / dense / fusion / rerank / hg_lookup
  planner.0                           # LLM generation 1개
  solve_subquery.0                     # 도구 span
    retrieval                         # 검색 단계별 순위와 관련 표
    sql.execute                       # 선택적 실행; 새 SQL 생성 LLM 아님
    observation                       # 다음 계획자가 실제 받은 자료
  planner.1 ...                       # 최대 6회
  evidence.assemble                   # 후보20 → 최종8, SQL 포함/제외
  answer                              # 별도 LLM generation 1개
  output.validate                     # 상태·형식·인용
```

평가 호출은 별도 evaluation trace 또는 명시적으로 분리한 observation에 저장하고 원래 request trace ID를 연결한다. 서비스의 토큰·응답 시간에 Judge 비용을 합치지 않는다.

### 기록할 내용

| 단계 | 필수 내용 |
| --- | --- |
| 요청 | request_id, question_id, run_id, 환경, 코드·설정·데이터·모델·프롬프트 지문 |
| 검색 | 실제 질문, BM25/vector 순위, RRF 후보, 중복 제거, 리랭커 입력 길이·점수·순위, HG 분기 |
| 계획 | 모델·reasoning·timeout, 실제 지침과 관측 입력, tool call, 구조 검증, 종료 사유 |
| 도구/SQL | 도구 인자, 앞서 허용된 표, 실제 읽은 표, 실행 여부·시간·상태·행 수, 절단, 원본 lineage |
| 문맥 | 후보와 최종 출처 순서, 제외 사유, 포함 SQL, 실제 모델 입력 문맥·hash·token count |
| 답변 | 모델 입출력, 상태, 인용, 검증 오류 |
| 사용량 | 계획/답변의 논리 호출·제공자 시도·재시도, 알려진 입출력/추론/캐시 토큰, 누락 여부 |
| 지연 | 대기·검색·SQL·계획·답변·출력검증·전체 요청 시간. 모델 로드·평가 시간은 별도 |
| 추적 자체 | trace ID, 전송 상태, flush/조회 확인, 점수 게시 상태 |

raw rank와 KURE score는 다른 척도이므로 하나의 score 필드로 덮어쓰지 않는다. 순위의 최초 누락 지점을 판단할 수 있게 단계 결과를 남긴다. 모든 검색의 합집합을 최종 후보20으로 부르지 않는다.

### 구현 연결

Langfuse SDK v5는 `langchain-core>=1`을 요구하고 `from langfuse.langchain import CallbackHandler`를 제공한다. graph invocation의 callbacks/metadata/tags로 연결하고 일반 Python 검색·SQL 작업에는 수동 observation을 사용한다. 동일 LLM을 callback과 수동 generation 양쪽에서 중복 생성하지 않는다. [공식 통합](https://langfuse.com/integrations/frameworks/langchain)

요청 ID는 UUID 등 앱 식별자를 유지하고 trace ID와 매핑한다. 두 값을 같은 문자열로 만들기 위해 임의 SDK ID 형식을 가정하지 않는다. 비동기·작업 스레드 경계를 넘겨도 부모 trace와 request context가 유지되는지 동시 요청 테스트로 확인한다.

종료·평가 작업 완료 시 flush 후 실제 저장된 trace와 scores를 조회해 확인한다. 전송 메서드가 예외 없이 끝났다는 것만으로 게시 완료라고 쓰지 않는다. trace 누락은 추적 실패로 남기며 로컬 결과를 삭제하지 않는다.

### 저장 범위

프로젝트의 승인된 평가·대표 재현에는 **실제 질문·검색 결과·최종 문맥·모델 입출력**을 확인할 수 있어야 한다. 승인된 Langfuse 공간 또는 접근 제한된 로컬 설치에서 보존하고, metadata만 기록한 실행은 이 요건을 충족했다고 하지 않는다.

일반 운영은 원문·개인정보 마스킹과 보존 기간을 별도 설정한다. 비밀키·인증 헤더·내부 추론은 기록하지 않는다. 원문·정답 인용은 Git 보고서에 복사하지 않는다. 이번 문서 작성으로 외부 전송이나 새 유료 실행을 시작하지 않는다.

## 3. 평가 세트와 분모

| 세트 | 용도 | 주의 |
| --- | --- | --- |
| 공식 Advanced 60 | 전체 동작·답변·판단 평가 | 최근 파일 기준 근거 없는 AS-DEV12·AS-ADD12를 제외한 페이지 근거 평가는 58. 두 질문도 동작·판단 평가에는 남음 |
| 카테고리 220 | 직접/간접·복합·일상 표현·모호함별 회귀 | 근거형 200, 범위 밖 20. 범위 밖은 검색 정답률과 분리하고 행동 평가에는 포함 |
| 고정 재생 표본 | 이식 동등성·에러 경계 | 새 LLM 정확도나 실사용 전체 품질의 증거로 사용하지 않음 |
| Judge 보정 표본 | 자동 채점 오류 확인 | 사람이 검수하지 않았다면 AI 검수라고 표시 |
| 별도 미사용 세트 | 마지막 채택 판단 | 기존 공식·카테고리를 미사용 검증이라고 부르지 않음 |

문항 수를 코드에 고정하지 않는다. manifest의 실제 ID와 역할을 기준으로 집계하고, 파일이 바뀌면 dataset version을 새로 만든다. 사용자 질문의 ‘57·160·180’ 같은 이전 수량과 현재 파일의 60·220을 혼용하지 않는다.

모호한 문항은 조용히 제외하지 않는다. 개인 정책대로 근거형 주 분모에 남기고 성공으로 세지 않으며 적용 범위 모호성을 표시한다. 모호성을 제외한 보조 수치를 제공하면 별도 분모를 명시한다. 참고 근거 자체가 없는 문항의 페이지 Recall은 N/A다.

## 4. 단계별 지표

### 검색과 최종 근거

| 지표 | 계산·의미 |
| --- | --- |
| 공식 문서 Hit@k·Recall@k | 실제 최종 문맥의 고유 book_id와 참고 문서 집합 비교. 발제문 필수 지표 |
| 페이지 Recall@20 / 최종8 | 정확한 book_id+page를 사용. 평균(문항별 macro)과 총 근거 집계(micro)를 구분 |
| 후보 전체 근거 충족 | 최종 후보20에 질문의 모든 필수 사실 그룹을 만족하는 허용 출처가 있는지 |
| 최종 전체 근거 충족 | 실제 최종 문맥에 모든 필수 사실이 남는지. SQL·원문 출처와 실제 남은 내용도 확인 |
| MRR·nDCG | 참고 출처 relevance 정의와 cutoff를 고정한 보조 순위 지표 |
| 단계별 손실 | 검색 Top50 → RRF/중복제거20 → HG/여러 결과 병합20 → 최종8의 회복·유실 |
| 도구 실행 | 검색 수, SQL 요청/검증/실행/성공/빈 결과, 반복 수, 호출 없이 종료한 비율 |

페이지가 후보에 있다는 것과 문맥 절단 뒤 필요한 사실이 실제로 있다는 것은 다르다. 참고 페이지 기반 계산과 내용 검수를 분리한다.

대체 근거 검수는 한 조건이라도 실패한 문항의 **모든 비교 조건 출력**에 같은 기준으로 수행한다. 검색되지 않은 페이지를 답변의 근거로 소급 인정하지 않는다. 같은 대상·시점·상태·단위·통화·범위를 뒷받침해야 한다. 보정 전 지정 점수·페이지 Recall은 유지하고, 보정된 근거 충족률을 별도 열로 낸다. 검수 미완료는 최종 결론으로 표시하지 않는다.

Ragas ContextPrecision/ContextRecall은 보조 지표다. 참고 답변의 주장과 문맥을 비교하는 모델 판정이므로 정확한 페이지 Recall이나 원문 검수를 대체하지 않는다. [Ragas Context Recall](https://docs.ragas.io/en/v0.4.3/concepts/metrics/available_metrics/context_recall/)

### 계획과 SQL

| 대상 | 기본 검증 | LLM Judge를 사용하는 부분 |
| --- | --- | --- |
| 계획 형식 | strict schema, 1 tool/call, 관측 table ID, 5 solve/6 planner 제한 | 불필요 |
| 계획 의미 | 원문 대상·기록·날짜 종류를 확인할 검수 표본 | 필요한 요청 누락, 다른 대상 확정, 실제/예정 혼동, 불필요한 반복·성급한 종료 |
| SQL | allowlist, 읽기 전용, 결과·Decimal·단위·출처, expected result와 로컬 비교 | 조건·범위·집계 의도 정합성의 보조 검토 |
| 도구 관측 | 원본 발췌·SQL 반환·절단·오류의 정확한 전달 | 하위 질문에 충분한 근거인지 진단 |
| 최종 상태 | 형식·상태 매핑 | 답변/추가 확인/보류 판단 적절성 |

계획 의미 정확도는 별도 대상·요청 정답이 있는 검수 표본에서만 수치로 주장한다. 전체 220문항에 이런 정답이 없으면 호출 통계와 사례 진단을 보고한다. 특정 tool sequence와 다르다는 이유만으로 의미상 같은 계획을 오답으로 만들지 않는다.

Ragas ToolCallAccuracy는 허용 tool call 정답이 있는 표본에서만 보조로 사용한다. AgentGoalAccuracy를 계획 자체의 정확도라고 부르지 않는다. SQL 문자열 일치보다 실행 결과와 범위가 우선이며, SQLSemanticEquivalence는 실제 결과 검증의 대체가 아니다. [Agent 지표](https://docs.ragas.io/en/v0.4.3/concepts/metrics/available_metrics/agents/) · [SQL 지표](https://docs.ragas.io/en/v0.4.3/concepts/metrics/available_metrics/sql/)

A에는 중간 답변 LLM이 없다. 계획·도구 진단을 위해 새 중간 생성 단계를 넣지 않는다.

### 최종 답변

같은 답변에 **Ragas Faithfulness와 4축 업무 Judge를 모두 적용**한다.

| 평가 | 입력·판정 |
| --- | --- |
| Faithfulness | 질문, 실제 답변, 실제 최종 원문+SQL 문맥. 답변의 사실 주장을 문맥이 뒷받침하는 비율 |
| Judge 사실 0/1 | 필요한 사실·수치가 정확하고 빠짐없는지 |
| Judge 조건 0/1 | 대상·날짜 종류·시점·단위·통화·정정·적용 범위가 맞는지 |
| Judge 근거 0/1 | 중요한 주장을 실제 문맥과 연결된 인용이 뒷받침하는지 |
| Judge 판단 0/1 | 답변·추가 확인·보류 중 적절한 행동과 이유인지 |
| 로컬 출력 검증 | schema·존재하는 최종 출처 ID·출처 매핑. 신뢰할 기대값이 있으면 수치/날짜도 검사 |

Judge 입력에는 질문·답변·상태·인용·실제 문맥·참고답·rubric을 제공한다. 출력은 각 축 점수, 이유, 뒷받침/모순 출처, 평가 불가 사유로 고정한다. 긴 추론을 요구하지 않고 원문을 확인할 짧은 이유를 받는다.

Faithfulness가 높아도 잘못 적용한 계약의 문맥을 충실히 따라 답할 수 있다. 정답 정확도·적용 조건·추가 확인 판단은 별도로 본다. 자동 평가의 이유가 원문을 가리키는지도 검수한다. [Faithfulness](https://docs.ragas.io/en/v0.4.3/concepts/metrics/available_metrics/faithfulness/)

FactualCorrectness와 AnswerRelevancy는 진단 필요 시 추가한다. 후자는 평가 LLM과 평가 임베딩을 함께 요구하므로 초기 필수 지표에 무조건 넣지 않는다. 선택하면 native provider/client와 Ragas `embedding_factory`로 평가 임베딩을 연결하고 deprecated LangChain embeddings wrapper를 추가하지 않는다.

## 5. Ragas API와 Judge 보정

현재 프로젝트는 `ragas==0.4.3`, `langchain-community==0.4.1`을 고정했다. 버전별 예제를 섞지 않는다. 현재 `app/evaluation/graders.py`의 native provider client + `llm_factory`와 `ragas.metrics.collections.Faithfulness`를 활용할 수 있다.

0.4 컬렉션 API는 `ascore(...)`에 필수 키워드 입력을 넘기고 반환의 value/reason을 저장한다. 구형 SingleTurnSample + single_turn_ascore 사용법과 혼합하지 않는다. LangChain을 적극 사용하더라도 deprecated LangchainLLMWrapper를 평가기에 억지로 추가하지 않는다. [0.4.3 이전 가이드](https://docs.ragas.io/en/v0.4.3/howtos/migrations/migrate_from_v03_to_v04/)

역할별 모델 설정을 분리한다.

| 역할 | 계획 |
| --- | --- |
| 검색 계획·답변 | A의 GPT-6 Luna medium 출발값 |
| Faithfulness·업무 Judge | 별도의 evaluator 설정. 같은 모델을 사용해도 별도 호출·프롬프트·사용량으로 기록 |
| 평가 보조 임베딩 | 선택한 지표가 필요로 할 때만 구성. 서비스 임베더와 같은 것으로 가정하지 않음 |

사용자가 다른 모델을 허용하기 전에는 평가 목적으로 더 큰 모델을 임의 추가하지 않는다. 같은 모델의 자기 편향 가능성은 사람/독립 검수 표본과 불일치 분석으로 점검한다.

보정 순서는 고정 표본 선정 → 원문 기준 라벨·허용 대체 근거 검수 → Judge 결과와 비교 → rubric 수정 → 버전 동결이다. 일치율, 항목별 오판, 통과/실패별 precision·recall, 반복 채점 일관성을 본다. 검수자는 AI인지 사람인지 기록하며 AI 검수만으로 사람 검수 완료라고 하지 않는다. 보정은 개발 표본에서 하고 최종 평가 결과를 보고 같은 실행의 rubric을 바꾸지 않는다. [Langfuse Judge 보정](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)

## 6. 실패와 비용 처리

| 상황 | 처리 |
| --- | --- |
| 서비스 답변 생성 실패 | 시스템 오류 건수에 포함. Judge 판단 0, 출력이 없어 검증할 수 없는 축은 평가 불가 |
| Judge/Ragas timeout·형식 오류 | 해당 점수 null/평가 불가와 원인. 서비스 오답 0으로 바꾸지 않음 |
| 사실 주장 없는 적절한 확인 질문 | Faithfulness N/A. 판단 축은 평가 |
| 사실 주장이 포함된 확인·보류 | 주장에 Faithfulness 적용, 행동은 판단 축으로 평가 |
| 참고 근거 없는 문항 | 문서/페이지 검색 지표 N/A, 상태·답변 판단은 평가 |
| 사용량을 받지 못한 호출 | unknown/partial. 0토큰 또는 0비용으로 표시하지 않음 |
| 검수 미완료 | 잠정 결과. 최종 채택 판단 유보 |

평균은 0을 포함하고 N/A·평가 불가는 제외하되, 분모·N/A·평가 불가·오류 수를 항상 표시한다. 검색 실패로 빈 후보가 나온 근거형 문항은 0이다.

계획·답변·Ragas·업무 Judge·검수 비용을 분리한다. Ragas 한 지표가 내부적으로 여러 LLM 호출을 할 수 있으므로 “문항당 Judge 한 번”으로 예산을 추정하지 않는다. 토큰·시도 수·요청당 상한·전체 실행 범위를 먼저 고정한다. Judge는 웹 요청 지연에 포함하지 않지만 전체 평가 비용에는 포함한다.

## 7. 실험 기록과 점수 게시

run manifest에 다음을 고정한다.

- 코드/스냅샷/질문/참고답 버전과 해시, 모델 revision, 프롬프트 버전과 hash.
- 검색·후보·문맥·SQL 예산, device/dtype/thread/batch/concurrency.
- timeout/retry/최대 solve, evaluator 모델·rubric·Ragas 버전.
- 결과 저장 상태, 채점 완료 상태, 검수 완료 상태, Langfuse 게시 확인 상태.

Langfuse에는 run_id·question_id·trace_id를 연결하고 `retrieval.page_recall_8`, `evidence.final_all`, `answer.faithfulness`, `judge.condition`처럼 의미를 구별한 이름을 쓴다. 원 점수와 검수 반영 점수는 별도 metric/version으로 둔다.

재게시 키는 trace·평가 버전·metric으로 만들되, 키만 같으면 중복이 사라진다고 가정하지 않는다. Langfuse 문서상 갱신에는 score_id, name, 날짜 기준 timestamp가 모두 같아야 한다. 최초 timestamp와 전체 score payload를 저장해 같은 값으로 재전송하고, 설치된 SDK/서버에서 중복 수를 실제 확인한다. 새 rubric은 새 평가 버전으로 저장한다. Langfuse SDK의 experiments/dataset run 기능을 사용할 수 있지만 기존 저장→채점 분리 원칙을 유지한다. [SDK experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk) · [Score 게시](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk)

프롬프트 원본은 먼저 Git에 두고 정확한 hash를 기록한다. Langfuse Prompt Management를 나중에 쓰더라도 평가에서는 변경 가능한 production label만으로 버전을 식별하지 않는다. 정확한 prompt version을 고정한다.

## 8. 보고서와 최종 검사

보고서에는 유형별 후보/최종 근거 충족, 문서·페이지 Recall, 답변 Faithfulness·4축 Judge, 개선/하락, 최초 누락 단계, 계획·SQL 오류, 호출·토큰·전체 지연을 별도 표로 낸다. 원 점수와 대체 근거 검수 반영 점수를 함께 둔다.

발제문 필수의 Dense / Hybrid / Hybrid+Reranking 비교도 유지한다. 같은 데이터·문맥 예산·답변 프롬프트·생성 모델의 단순 검색 비교와, 반복 계획/SQL까지 있는 TableRAG A의 전체 시스템 비교를 분리한다. TableRAG의 이득 전체를 리랭커 하나의 효과라고 부르지 않는다.

보안 질문 2개는 같은 문맥을 먼저 저장한 후 기본/방어 프롬프트를 각각 적용한다. 공격 라벨을 RAG 입력에 넣지 않는다. 계획 도구의 미관측 표·쓰기 SQL 방어는 별도 가짜 도구 테스트로 검사한다.

출시 전 필수 확인:

1. 같은 최종 문맥 지문이 답변 입력·로컬 기록·Langfuse에서 일치한다.
2. 모든 RAG LLM 호출과 실패/재시도가 계수되고, Judge 호출은 분리된다.
3. API가 사용한 진입점과 평가 진입점이 같다.
4. 평가 참고답·판정 결과가 검색·계획·답변에 들어가지 않는다.
5. 공식/카테고리의 분모·대체 근거·범위 밖·모호 문항이 설명된다.
6. 완료된 score의 실제 게시를 확인하며, 로컬 JSONL만으로 Langfuse 연동 완료라고 하지 않는다.
