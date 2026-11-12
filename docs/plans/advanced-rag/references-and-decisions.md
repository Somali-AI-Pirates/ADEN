# 조사 근거와 구현 결정

> 2026-11-12 현재 구현 기준은 DB 우선 통합 TableRAG다. [현재 구현 기록](unified-implementation-log.md)과 [빠른 시작](../../guides/quickstart.md)을 따른다. 아래 A 이식 내용은 초기 설계 기록이다.

확인일: 2026-11-11. 현재 사실, 사용자 선택, 제안, 구현 후 확인할 내용을 분리한다. 런타임 구현·새 벤치·유료 모델 호출은 이번 문서 작업에 포함하지 않았다.

## 1. 현재 근거와 해석

| 근거 | 확인한 내용 | 계획에 반영한 판단 |
| --- | --- | --- |
| [TableRAG 기준선](../../../experimental/u_10o0o/records/advanced-table-rag-preservation-20261111/baseline.md) | 현재 KURE-v3·KURE nano·5:5, 근거 보존 B는 기본 미채택 | 사용자도 A를 명시 선택. A를 먼저 이식 |
| [A 코드](../../../experimental/u_10o0o/table_rag/agent.py) | solve 5회, 계획 최대6회, 별도 답변1회. SQL을 solve 인자에 포함 | 도구 계약과 호출 상한 보존 |
| [검색·조립 코드](../../../experimental/u_10o0o/table_rag/retrieval.py) | 페이지+IS 표 검색, 페이지 중복 제거, KURE, HG 보충, 20/8/8,000 | 단순 문서 retriever로 축소하지 않음 |
| [HG 조회](../../../experimental/u_10o0o/table_rag/hg.py) | 기존 보충과 검증된 날짜 조건 원문 조회 | 외부 함수 의존성을 조사해 제품 코드로 옮길 대상 |
| [표 저장소](../../../experimental/u_10o0o/table_rag/store.py) | 원본 물리 표와 셀 출처, Decimal, SQLite authorizer | 단순 문자열 SQL executor로 대체하지 않음 |
| [공식·카테고리 A/B](../../../experimental/u_10o0o/records/advanced-table-rag-official-category-20261111/report.md) | 지정 근거와 대체 근거 검수의 차이, 일반/일상 표현별 하락 가능성 | 기존 점수·검수 반영 점수·답변 채점을 구분 |
| [단계 분리 비교](../../../experimental/u_10o0o/records/advanced-table-rag-workflow-20261111/report.md) | 별도 SQL 생성·중간 답변·다른 출처 보존 경로 | 사용자가 고른 A와 다른 구조. 새 설계에 혼입하지 않음 |
| [실험과 논문 대응](../../../experimental/u_10o0o/records/advanced-table-rag-20261111/paper-coverage.md) | 논문을 현 데이터에 적용했으나 동일 모델·평가 재현은 아님 | TableRAG 논문 재현 완료나 정답 보장으로 설명하지 않음 |
| [공통 계약](../../design/contracts.md), [타입](../../../shared/contracts.py) | 실제 HTTP는 question+선택 retrieval_mode | 질문 전용 API는 현재 사실이 아닌 전환 제안 |
| [평가 정책](../../../experimental/u_10o0o/evaluation-policy.md) | 실패 문항과 상대 출력 검수, 대체 근거, 모호성·분모 처리 | 새 비교 보고서에도 같은 검수 기준 적용 |
| [프로젝트 요구](../../project-brief.md) | LangChain·Langfuse, 검색3조건, Faithfulness와 별도 Judge | TableRAG 전체 비교와 과제 검색3조건 비교를 분리 |

현재 공식 A의 지정 근거 충족 48/58과 검수 반영 57/58은 **서로 다른 평가 기준**의 점수다. 카테고리 근거형 A도 188/200과 195/200을 구분한다. 해당 실행은 검색과 문맥 평가이므로 답변 정확도 98.3% 또는 97.5%로 인용하면 안 된다. 상세 수치의 기준 위치는 위 A/B 보고서이며 이 문서는 구현 목표 점수로 재정의하지 않는다.

기존 baseline 문서는 KURE-v2 비교 종료를 기록하고 있다. 이번 사용자 요청은 최종 모델 비교 가능성을 다시 열어 두되 **구조는 A**로 정한 것이다. 이 계획은 과거 선택 기록을 덮어쓰지 않고 새 모델 결정 전까지 v3를 출발값으로 쓴다.

## 2. 설계 결정과 대안

| 결정 | 적용 이유 | 이번에 선택하지 않은 대안 |
| --- | --- | --- |
| 명시적 상태기계 | A의 반복·종료를 그대로 표현하고 호출 상한을 읽을 수 있음 | create_agent 기본 루프에 의미를 맡기는 방식 |
| 함수 조합 | RRF·중복 제거·출처 선택의 입력/출력이 눈에 보임 | 상속으로 기존 실험 실행기 전체 재사용 |
| 생성 시 의존성 주입 | 모델·스냅샷을 한 번 준비하고 테스트에서 가짜 자원 주입 | 모듈 전역 가변 자원, 요청마다 모델 로드 |
| 필요한 검색기만 어댑터 | v2의 다중 벡터와 v3의 단일 벡터 차이를 격리 | 모델 이름 문자열만 교체하는 범용 Embeddings 가정 |
| 요청 상태와 공유 자원 분리 | 여러 요청의 관측 표·계획 이력 혼합 방지 | 공유 agent 객체에 대화 상태 누적 |
| 관측은 callback/span | 품질 평가 때문에 서비스 인자에 run_record를 전파하지 않음 | 모든 함수가 로그 dict를 수정 |
| 읽기 전용 스냅샷 | 검증한 문서·표·벡터의 한 버전을 사용 | 서버 실행 중 색인 일부만 갱신 |
| 실행 후 평가 | gold 유입과 사용자 응답 지연 방지 | 웹 요청 중 Judge 동기 호출 |
| Git prompt가 첫 기준 | 시작 단계의 운영 지점을 줄이고 정확한 버전 재현 | Git과 원격 prompt 두 곳을 동시에 정본으로 운영 |

StateGraph 사용은 이 프로젝트에 대한 설계 권고이며 논문이나 LangChain이 반드시 요구하는 것은 아니다. 작은 노드 함수와 명시적 반복으로 A를 옮기고, 장기 중단·재개 요구가 생기기 전에는 checkpointer·외부 상태 저장소를 추가하지 않는다. 그래프가 요청별 상태의 수명주기를 대체해 주는지도 직접 확인한다.

## 3. 공식 레퍼런스와 적용 범위

### TableRAG와 모델

- [TableRAG: A Retrieval Augmented Generation Framework for Heterogeneous Document Reasoning, EMNLP 2025](https://aclanthology.org/2025.emnlp-main.710/) — 텍스트와 표를 함께 다루며 검색·SQL·반복 추론을 연결한 연구다. 현재 A는 이 발상을 프로젝트에 맞게 적용했으며 논문의 중간 답변 단계를 별도 호출로 모두 복제한 구현이 아니다.
- [저자 공개 구현](https://github.com/yxh-y/TableRAG) — 오프라인 준비와 온라인 실행의 분리를 참고한다. 평가용 table ID 힌트나 데이터셋 전용 가정을 사용자 질의에 주입하지 않는다.
- [KURE-v2 모델 카드](https://huggingface.co/nlpai-lab/KURE-v2) — 토큰별 다중 벡터와 MaxSim. v3 단일 벡터와 검색기 차이를 분리할 근거다.

### LangChain·LangGraph

| 공식 자료 | 적용 |
| --- | --- |
| [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) | state·node·edge와 조건부 루프 |
| [Agents](https://docs.langchain.com/oss/python/langchain/agents) | create_agent의 도구 루프와 A 제어 흐름을 구분 |
| [Tools](https://docs.langchain.com/oss/python/langchain/tools) | Pydantic 도구 인자, 런타임 자원을 모델 스키마 밖에 두는 방식 |
| [RunnableSequence](https://reference.langchain.com/python/langchain-core/runnables/base/RunnableSequence) | 프롬프트와 모델을 작은 체인으로 연결 |
| [RunnableConfig](https://reference.langchain.com/python/langchain-core/runnables/config/RunnableConfig) | callbacks·metadata·tags 전파 |
| [ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai) | tool binding·구조화 출력·Responses 메시지 전달. 실제 고정 버전에서 검증 필요 |
| [EnsembleRetriever](https://reference.langchain.com/python/langchain-classic/retrievers/ensemble/EnsembleRetriever) | 기본 fusion/dedupe를 A와 같다고 가정하지 않는 근거 |
| [CrossEncoderReranker](https://reference.langchain.com/python/langchain-classic/retrievers/document_compressors/cross_encoder_rerank/CrossEncoderReranker) | rerank와 최종 원문 조립은 별도 단계로 유지 |
| [보안 정책](https://docs.langchain.com/oss/python/security-policy) | DB 권한 제한과 다층 검증. 프롬프트만으로 쓰기·임의 접근 차단을 보장하지 않음 |

LangChain 적극 사용은 모든 검색 계산을 프레임워크 내부로 숨기는 뜻이 아니다. 표준 문서·모델·도구·실행·추적 인터페이스에서 재사용하고, ADEN의 점수·HG·예산 규칙은 명시적인 함수로 남긴다.

### Langfuse·Ragas

| 공식 자료 | 적용 |
| --- | --- |
| [Langfuse LangChain 통합](https://langfuse.com/integrations/frameworks/langchain) | SDK v5 CallbackHandler와 langchain-core 1 호환 |
| [SDK experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk) | 실행과 항목/실험 평가 연결, 로컬 데이터 사용 |
| [평가 데이터 모델](https://langfuse.com/docs/evaluation/experiments/data-model) | dataset item·trace·점수 연결 |
| [Score 게시](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk) · [재게시 FAQ](https://langfuse.com/faq/all/tracing-data-updates) | trace/observation에 외부 지표 연결. score_id·name·timestamp를 함께 고정하는 재게시 계약 |
| [LLM-as-a-Judge](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge) | 기준 보정과 채점 불일치 확인 |
| [Ragas 0.4.3 migration](https://docs.ragas.io/en/v0.4.3/howtos/migrations/migrate_from_v03_to_v04/) | collections API·native client와 이전 API 구분 |
| [Faithfulness](https://docs.ragas.io/en/v0.4.3/concepts/metrics/available_metrics/faithfulness/) | 실제 답변의 주장과 실제 문맥 일치. 업무 정답 전체의 대체가 아님 |
| [Context Recall](https://docs.ragas.io/en/v0.4.3/concepts/metrics/available_metrics/context_recall/) | 참고 답변 기반 보조 평가와 페이지 Recall 구별 |
| [Agent 지표](https://docs.ragas.io/en/v0.4.3/concepts/metrics/available_metrics/agents/) | 허용 계획 정답이 있을 때 tool call 보조 평가 |
| [SQL 지표](https://docs.ragas.io/en/v0.4.3/concepts/metrics/available_metrics/sql/) | 결과 표 비교·SQL 의미 평가의 범위 |

## 4. 의존성과 버전 확인 순서

문서 작성 당시 [pyproject.toml](../../../pyproject.toml):

| 항목 | 현재 선언 | 계획 |
| --- | --- | --- |
| Python | >=3.11, 개발/CI 안내 3.13 | 실제 실행 환경도 함께 기록 |
| langchain-core / langchain-openai | >=1,<2 | 고정 lock에서 tool/structured output/callback 확인 |
| langgraph | `>=1,<2`, 잠금 파일 1.2.20 | 계획·도구 반복에 직접 사용 |
| langfuse | >=5,<6 | v5 API 사용. 과거 v2/v3 예제 혼용 금지 |
| ragas | 0.4.3 | collections API 유지 |
| langchain-community | 0.4.1 | Ragas import 호환을 위한 pin을 무심코 풀지 않음 |
| sentence-transformers | >=6.2,<7 | 선택 모델과 로컬 revision으로 검증 |

패키지를 “최신으로 전부 올리기”부터 하지 않는다. 먼저 현재 lock·설치 버전을 확인하고 추가할 graph 관련 최소 의존성을 선택한다. 다음 smoke를 사용자 구현 단계에서 수행한 후 버전을 고정한다.

1. 네트워크 없는 Document·prompt·도구 JSON schema·가짜 graph 실행.
2. CallbackHandler 생성과 로컬 가짜 generation 연결.
3. Ragas 컬렉션 import·가짜 client 입력/출력 계약.
4. 승인된 소규모 실제 호출에서 tool call ID·메시지 왕복·구조화 출력·usage 확인.
5. 타깃 장비의 임베딩·rerank 실행과 wheel 리소스 검사.

현재 서버의 LangChain 생성 체인과 Ragas native-client 사용은 재사용 가치가 있다. API service 전체 구조나 가변 run_record 전달까지 그대로 복제할 필요는 없다.

## 5. 아직 확정하지 않은 것

- 최종 임베딩과 리랭커. A의 상위 흐름은 고정하되 모델별 검색 방식과 자산을 명시한다.
- 배포 CPU/GPU·메모리, 동시 요청, p95 목표, 전체 요청 timeout와 대기열.
- 실제 설치할 LangGraph 등 세부 버전. 공식 API 확인은 설치된 조합의 실행 검증을 대신하지 않는다.
- Langfuse 저장 위치·접근 권한·보존 기간과 운영 원문 마스킹. 대표 평가는 요구되는 실제 근거 확인이 가능해야 한다.
- 미사용 검증 세트와 사람 검수 확보. 미확보이면 그 한계를 보고하고 사람 검수 완료라고 쓰지 않는다.

이 항목들은 문서 작성 차단 사유가 아니다. 각각 의존하는 구현·실행 단계에서 결정한다.

## 6. 조사 과정과 파일 지문

1. 사용자 선택을 확인해 TableRAG A를 기준으로 범위를 좁혔다.
2. 현재 agent/retrieval/store/HG 코드와 기준선·비교 보고서·발제문·평가 정책을 대조했다.
3. LangChain/Langfuse/Ragas 공식 자료를 독립 조사하고 부모 작업에서 주요 문서를 재확인했다.
4. 직접 구현 단계와 관측·평가를 문서로 작성했다. 기존 서버·실험 코드·설정·데이터는 수정하지 않았다.
5. 독립 문서 검토와 로컬 링크·문서 구조·diff 검사를 완료했다. 결과는 아래 완료 기록에 남겼다.

아래 SHA-256은 이번 조사에서 읽은 **작업 트리 파일**의 지문이다. 실행한 새 벤치의 지문이 아니며 전체 전이 의존성 manifest도 아니다. 실제 이식은 로드맵 0단계에서 실험 A의 입력·의존 코드까지 다시 고정한다.

| 파일 | SHA-256 |
| --- | --- |
| [experimental/u_10o0o/table_rag/agent.py](../../../experimental/u_10o0o/table_rag/agent.py) | `5f67c3be87326319d4b83bf07dd7fda1595fe00a76075cc2edfb07345b6570b1` |
| [experimental/u_10o0o/table_rag/retrieval.py](../../../experimental/u_10o0o/table_rag/retrieval.py) | `d08e66fd0256ea9807ad207234097e3b33b9d40771f8bd4929ca310f58ac3569` |
| [experimental/u_10o0o/table_rag/store.py](../../../experimental/u_10o0o/table_rag/store.py) | `525afb18cb17d5fe79be8d5a186071ec994fe8e8fa522b9db315e6e59ec82c2c` |
| [experimental/u_10o0o/table_rag/hg.py](../../../experimental/u_10o0o/table_rag/hg.py) | `91a4715115f230d6abf48cdab325a978b0f9e805dbea69956df225d974fd8085` |
| [experimental/u_10o0o/records/advanced-table-rag-preservation-20261111/baseline.md](../../../experimental/u_10o0o/records/advanced-table-rag-preservation-20261111/baseline.md) | `67bfb9552a9b544c0582d824fa02b338f06454903a2d0460819069ecbeda4562` |
| [pyproject.toml](../../../pyproject.toml) | `1420d9e4031c556a8fa7f027d35cda8dafc6a97c7f184ea2e8c3cee285ae4531` |

## 7. 이번 문서 작업 완료 기록

- 독립 검토: A 코드/이식 경계 검토와 평가/라이브러리 검토를 각각 수행하고 지적 반영 후 재확인했다.
- 반영한 사항: 모델 비교 시점, planner 누적 history 예산, 재시도 0회, DatasetItem의 정답 분리, LangGraph 직접 의존성 계획, Ragas 문서 버전, 점수 재게시 시 timestamp 보존.
- 로컬 검사: 다섯 Markdown 파일의 UTF-8·코드 블록 짝·공백과 로컬 링크 42개 확인, 깨진 로컬 경로 없음. git diff --check 통과.
- 문서 범위: 이 폴더의 계획 네 개와 docs/README.md 탐색 링크 한 줄. 기존 experimental 변경은 보존했다.
- 미실행: 런타임 구현, 패키지 설치, Ruff·pytest·build, 모델·평가 API 호출, Langfuse 업로드. 문서만 바꿨으므로 이를 구현 검증 완료라고 표시하지 않는다.
- 커밋·푸시·병합은 이번 계획 작성 범위에 포함하지 않았다.
