# DB 우선 통합 TableRAG 구현 기준

2026-11-12 사용자 확정 기준. 실험의 `table_rag_unified`에서 `db_first=True, verify_evidence=False`인 흐름을 제품으로 옮긴다. 요청별 요구 목록·인용 제출을 검사하는 기능과 ON/OFF 설정은 제품에 제공하지 않는다.

실행은 [빠른 시작](../../guides/quickstart.md), 완료된 검사와 남은 범위는 [구현 기록](unified-implementation-log.md)을 따른다. 벡터 저장소 연결과 동등성 검사는 [Qdrant Cloud 적용 기록](qdrant-cloud-migration.md)에 남긴다. [초기 A 설계](initial-table-rag-a-plan.md)와 [단계별 학습 로드맵](implementation-roadmap.md)은 이전 선택의 이력이다.

현재 승인된 로컬 구조 정리의 단계·검증 결과와 재개 지점은 [로컬 구조 정리 진행 기록](local-refactor-progress.md)을 따른다. 기존 Qdrant Cloud·Langfuse 연결과 검색·답변 계약은 유지한다.

코드 가독성·설정·로컬 인프라·운영에 대한 후속 제안은 [구조·운영 검토](architecture-operations-review.md)에 정리한다. 해당 문서는 개선 검토이며 이 문서의 채택 기준이나 현재 실행 방법을 변경하지 않는다.

후속 작업의 파일별 변경·로컬 환경·CI/CD·aden 서브도메인 배포는 [구조 정리·배포 구현 계획](architecture-operations-implementation-plan.md)을 따른다. 클라우드 대상과 완료 여부는 해당 문서에서 별도로 관리한다. 실제 적용한 DNS·HTTPS·GitHub 환경은 [클라우드 준비 기록](cloud-setup-record.md)에서 확인한다.

## 1. 범위와 호환 경계

공통 API·RAG CLI·TableRAG 평가 CLI는 같은 `RagService`를 사용한다. 명시 코드가 모두 출처 있는 유일한 대상에 연결되면 공통 SQLite의 HG·HA·IS 기록을 먼저 조회한다. 코드가 없거나 확정할 수 없거나 제외 표현이 있으면 전체 페이지·표 검색으로 시작한다. 기존 HG 전용 보강은 사용하지 않는다.

필요한 추가 문서·규정은 원문 페이지와 IS 표 검색 단위에 대해 BM25·벡터 검색을 5:5 RRF로 합치고 KURE nano로 정렬한다. KURE-v3 임베딩과 Luna medium을 사용한다. 모델 리비전·입력 길이·정규화·검색 조건은 번들 및 실행 manifest에 남긴다.

기존 HTTP 응답 필드·요청 ID·답변 인용 검사를 유지한다. 별도로 남은 일반·보안 평가 및 B/C 주입 경로는 [B/C 평가 가이드](../../guides/local-bc.md)의 범위다. 제품은 개인 실험 모듈이나 개인 DB 경로를 import하지 않는다.

지원 범위에는 문서·표·공통 기록 조회와 읽기 전용 SQL만 포함한다. 쓰기 SQL, 웹 검색, 임의 코드 실행, 추가 답변 검증 에이전트, 요청별 근거 확인 토글은 포함하지 않는다.

## 2. 실행 흐름과 도구

1. 오프라인에서 검증한 번들과 고정 모델을 서버 시작 시 로드한다.
2. 질문의 명시 코드가 모두 유일하게 확정되면 DB 관측으로, 그렇지 않으면 하이브리드 관측으로 시작한다.
3. LangChain 모델·도구 계약을 LangGraph 반복에서 실행한다. 한 턴에 도구 하나, 최대 5회 작업 후 마지막 계획 호출은 finish만 허용한다.
4. 발견한 출처 중 finish가 선택한 출처를 최종 후보에 우선 보존한다. 나머지는 관측 묶음 순서로 채운다.
5. 후보 최대 20쪽에서 원본 최대 8쪽과 SQL을 조립한다. 실제 근거 JSON은 8,000토큰 이하이며, 충분 판정에 사용한 선택 출처가 빠지면 근거 부족으로 바꾼다.
6. Luna가 질문·최종 원문·SQL·종료 상태를 받아 답변을 생성한다. 최종 문맥 밖 인용과 종료 상태의 부당한 변경을 거절한다.
7. 요청 결과를 저장한 후 반환한다. 저장 실패는 request_id가 있는 오류 응답으로 알린다.

| 도구 | 계약 |
| --- | --- |
| lookup_records | 질문·기존 관측으로 뒷받침되는 대상 코드·속성·역할을 공통 기록 DB에서 조회 |
| search_evidence | 문서 하이브리드 검색 또는 표 카탈로그 검색 |
| read_sources | 이미 발견한 원본 페이지 읽기 |
| query_tables | 관측한 표 스키마에 한해 제한된 읽기 전용 SQL |
| finish | 상태·발견한 선택 출처·요약·충돌 제출 |

계획 모델의 원시 내부 추론은 저장하지 않는다. 관측·도구 인자·모델에 실제 전달한 메시지와 출력·사용량은 승인된 추적 범위에 남긴다.

## 3. 데이터와 코드 책임

| 위치 | 책임 |
| --- | --- |
| app/ingestion/scripts/prepare_table_rag.py | build·check·models·upload-qdrant·check-qdrant 진입점 |
| app/ingestion/table_rag_builder.py | 구축 조립·검증·잠금·재개 |
| app/ingestion/table_rag/ | 원문 추출·기록/표 저장·벡터 구축·단계 지문 |
| app/rag/config.py / shared/table_rag_policy.py | 공통 실행 설정 해석 / 고정 모델·검색 정책 |
| app/rag/corpus.py | 페이지·표 파일 읽기, 번들 지문·벡터 정렬·원본 출처 확인 |
| app/rag/records.py / table_store.py | 공통 기록 및 안전한 표 조회 |
| app/rag/retrieval.py | 페이지·표 BM25/벡터/RRF/KURE |
| app/rag/tools.py / graph.py | 도구 입력 스키마·관측 경계·State 갱신·반복 종료 |
| app/rag/state.py / tool_handlers.py | 요청 State·Runtime context / 상태를 변경하지 않는 네 도구 처리 |
| app/rag/evidence.py / model.py / service.py | 최종 근거·모델 호출·API/CLI 공통 실행 |
| app/rag/lifecycle.py | 취소 시 작업 완료 대기·한 번만 닫기 |
| app/observability/run_store.py / tracing.py | 요청별 로컬 저장 및 Langfuse |
| app/evaluation/table_rag_a.py | 저장된 통합 TableRAG 출력 채점 (기존 모듈 이름 유지) |

번들은 `artifacts/advanced-table-rag/`에 둔다. `records.sqlite3`는 대상·코드·필드·원본 연결, `tables.sqlite3`는 표 계산과 셀 출처를 담는다. 페이지·표 JSON과 벡터 배열, 구축·설정 manifest도 같은 번들에 둔다. 파일명에는 버전을 붙이지 않고 지문과 버전은 manifest로 관리한다. 기존 실험 산출물은 보존한다.

## 4. 실행 저장과 평가

기본 서버·CLI는 `artifacts/rag-runs/<request_id>/`의 `manifest.json`과 `runs.jsonl`에 질문·답변·실제 최종 근거·SQL·도구 실행·사용량·trace ID를 저장한다.

평가 CLI `app.evaluation.scripts.run_table_rag`는 이 결과를 그대로 읽는다. 참고답이 있으면 검색/SQL 지표와 4축 Judge를, 참고답이 없어도 Ragas Faithfulness를 실행할 수 있다. 참고답이 필요한 지표는 평가 불가 사유를 표시한다. Langfuse 점수는 원래 trace에 게시하고 같은 ID로 재게시·조회한다.

준비부터 평가까지의 명령은 [데이터 가이드](../../guides/data.md#advanced-tablerag-데이터)와 [평가 가이드](../../guides/c-module-guide.md#tablerag-평가)를 기준으로 한다.

## 5. 완료 검사

- 고정 계획 응답으로 실험과 제품의 기록·후보·SQL·최종 출처 대조.
- 모호한 코드·여러 대상·날짜 종류·미관측 출처/표·SQL 제한·최종 선택 유실·호출/시간 한도 검사.
- 동시 요청·저장 실패·외부 추적 실패 검사.
- 실제 8개 시나리오를 실행하고 저장 결과만 채점한 뒤 Langfuse trace·점수 조회.
- 개인 DB와 실험 산출물 없이 원본 기반 구축 확인.
- 데이터/동시성 및 도구/추적 경계 독립 리뷰, Ruff·전체 pytest·CLI·샘플·패키지 리소스 검사.

검사 결과를 설계의 완료 주장과 구분해 [구현 기록](unified-implementation-log.md)에 남긴다. 8개 연결 표본은 품질 벤치마크가 아니다.
