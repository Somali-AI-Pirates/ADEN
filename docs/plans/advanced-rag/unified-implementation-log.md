# DB 우선 통합 TableRAG 구현 기록

2026-11-12 사용자 확정 기준은 table_rag_unified의 db_first=True, verify_evidence=False다. 공통 HG·HA·IS 기록 조회, 페이지·표 하이브리드와 KURE nano, 다섯 계획 도구를 기본 API·CLI에 연결한다. 요청별 근거 확인 기능과 토글은 제품 범위에서 제거한다.

기존 HTTP 응답·요청 ID와 SQL 읽기 전용 제한을 유지한다. 제품 코드는 experimental 모듈과 개인 산출물 경로를 참조하지 않는다. 기존 로컬 데이터와 실험 결과는 보존한다. 런타임 번들은 artifacts/advanced-table-rag, 요청 기록은 artifacts/rag-runs/<request_id>를 사용한다. 모델·데이터 버전은 manifest에 기록한다.

사용자는 기존 .env 키와 Langfuse 프로젝트의 상세 질문·답변·근거 기록을 승인했다. GPT-6 Luna로 계획·생성·Judge·Ragas를 검증하며 서비스 결과 저장 후 채점한다. 실제 연동은 8개 표본과 Langfuse 조회로 확인한다.

## 구현과 검증 진행

- 시작 시 현재 작업 트리의 미커밋 구현을 확인했다. 공통 기록 조회, 계획 도구, 데이터 구축, 평가·추적을 분담했다.
- 공통 기록 로더는 기존 검증 인덱스 25,451개 대상의 대표 조회와 출처 역할·순서를 대조해 일치했다. 날짜 종류·모호한 규격·상충 조건과 표 카탈로그 검색 7건도 확인했다. 비교 중 원본 DB는 수정하지 않았다. 로컬 결과: artifacts/verification/table-rag/record-parity.json.
- API·서비스·요청 저장 집중 검사: 28 passed, 4 subtests passed. 전체 최종 검사는 아래 완료 기록에서 구분한다.
- 실제 연동용 8개 표본을 모델 실행 전에 고정했다. HG·HA·IS·규정 혼합·코드 없음·모호성·표 계산·원문 읽기 시나리오다. 기능 연결 확인용 표본이며 검색 품질 벤치마크로 해석하지 않는다. 원문·참고답·실행 결과는 Git에 넣지 않는 로컬 검증 경로에 둔다.
- 고정 계획 재생 2건(DB 시작, 표 발견 후 SQL)에서 초기 경로·후보 순서·SQL 값/상태·최종 출처·finish 상태가 기존 통합 실험과 일치했다. 실제 검색 순위와 모델 선택은 이 재생의 비교 대상이 아니다. 로컬 결과: artifacts/verification/table-rag/fixed-plan-parity.json.
- uv lock --check 통과(168개 패키지). 표 저장소·데이터 집중 검사 18개 통과.
- 최종 코드 검증과 실제 연동 결과는 아래 기록에 정리한다. 원본 기반 재구축 검증은 별도 경로에서 완료 여부를 기록한다.


## 독립 리뷰에서 반영한 경계

- 모델에 보내는 도구 응답과 추적 메시지의 역할·도구 이름·호출 ID를 일치시켰다.
- Decimal의 전역 기본 정밀도에 의존하던 합산을 고쳤다. SQL 원문도 결과에 연결해 최종 답변·Judge·Faithfulness에 같은 근거를 전달한다.
- 같은 평가 세트의 채점 조건 충돌과 동일 요청의 다른 grade 게시를 검사한다. 같은 grade의 재게시만 기존 점수 ID·날짜를 재사용한다.
- Langfuse 점수 확인은 trace별 묶음 조회로 바꿨다. 개별 점수마다 호출할 때 관측된 API 오류의 HTTP 상태는 당시 저장되지 않아 원인을 단정하지 않는다. 조회 오류는 이제 예외 유형·HTTP 상태만 기록한다. 응답 원문은 오류 로그에 넣지 않는다.
- 원본 줄바꿈 접합은 채택한 실험의 입력 재현 규칙을 유지한다. PDF의 줄바꿈이 단어 경계인지 문장 내부 감싸기인지 자동으로 확정하지 않으며, 이 정책 변경은 별도 검색 입력 변경으로 검증해야 한다.

## 통합 기준 갱신

작업 중 사용자가 main을 병합했다. 기준 HEAD는 5d2a8ac에서 63e7b71로 바뀌었다. 새 API 오류 처리와 Judge 출력 v2·계산 진단을 보존하고 현행 코드에서 통합 검증한다. HTTP 기록 변환에서도 TableRAG 계획·답변 호출과 SQL 최종 근거를 유지하고, 채점 조건에는 실제 서버 스냅샷과 Judge 스키마를 남긴다.

## 실제 실행과 평가

기존 .env 자격증명으로 GPT-6 Luna를 호출했다. `uv run --locked fastapi dev --host 127.0.0.1 --port 8765 --reload-dir app/api`로 같은 기본 앱을 실행했고 /health, /docs, /openapi.json을 확인했다. CPU 런타임에서 수행한 기능 연결 검사이며 별도 검색 품질 벤치마크가 아니다.

| 표본 | 초기 경로 | 응답 상태 | 최종 쪽/토큰 | Faithfulness | Langfuse trace ID |
| --- | --- | --- | --- | --- | --- |
| SMOKE-HG | records | answered | 5 / 2523 | 1.000 | f03a1d275d5494d19b40d104e4cfbf3c |
| SMOKE-HA | records | answered | 3 / 1975 | 0.857 | d42e48a4a6b728fd09bc011243d4712f |
| SMOKE-IS | records | answered | 1 / 532 | 0.800 | 71b90c4091b89d9bb2e235dc742289ea |
| SMOKE-MIXED | hybrid | answered | 8 / 6005 | 0.818 | 1ae0403e94b8ea679ae21379a440c8a0 |
| SMOKE-NOCODE | hybrid | answered | 8 / 4404 | 0.667 | 88abba201b80e609d2f73388025f60ce |
| SMOKE-AMBIGUOUS | hybrid | needs_clarification | 8 / 4478 | 0.400 | 7b58cade333e52c653cf4f431b3cee56 |
| SMOKE-SQL | records | answered | 1 / 651 | 1.000 | e8ff6f441900a440c1f16ac30b3fa62a |
| SMOKE-READ | records | answered | 1 / 532 | 0.833 | 6bd571e53d9a28e75dcef4fc89fff2c3 |

- 8/8 HTTP 200, 8/8 저장 답변과 응답 일치, 8/8 문맥 해시 일치. 실제 원격 trace에서 질문·답변·계획 모델·도구·최종 문맥 입출력을 다시 읽었다.
- 8건의 계획 17회·답변 8회를 HTTP trace에서 다시 복원했다. 보고된 입력/출력 토큰은 144,347/4,298이며 사용량이 알려지지 않은 호출은 없었다. SDK 보고량이며 별도 비용 추정은 하지 않았다.
- 별도 도구 표본 1건에서 DB로 시작한 뒤 lookup_records → search_evidence → read_sources → finish 흐름을 확인했다. 고정 8건과 합쳐 다섯 도구 모두 실제 실행됐으며, 추가 표본은 위 채점 분모에 넣지 않았다.
- SQL 표본의 최종 SQL 기대값 지표는 1.0이다. 실제 SQL 문자열·결과·원본 출처가 저장된 최종 근거와 평가 입력에 포함된다.
- 참고답 없는 저장 요청에도 Faithfulness 채점이 실행됐다. 참고답이 필요한 Judge/검색 지표는 평가 불가 또는 적용 대상 아님으로 남겼다.
- 기능 연결 성공을 답변 품질 전부 통과로 해석하지 않는다. HA·규정 혼합·모호성 표본에서 Judge 조건/근거/판단 감점이 있었고, Faithfulness도 위처럼 차이가 났다. 이 표본을 보고 채택한 프롬프트나 검색 규칙을 변경하지 않았다.
- 요청 로그는 실제 9건 각각 한 줄이고 Uvicorn의 중복 접근 로그는 없었다. 검색 중 health 요청과 정상 종료도 확인했다.

원문·답변·참고 근거는 로컬 `artifacts/verification/table-rag/`와 `artifacts/rag-runs/`에만 보관한다. 위 trace ID는 해당 프로젝트 접근 권한으로 조회할 수 있다. Langfuse 점수의 최종 게시·조회 결과와 전체 검사는 아래 완료 기록에서 구분한다.

## 동등성 확인

원래 실험의 검색 코드를 실제로 실행한 추가 3건에서 제품 코드와 초기 RRF 후보·KURE 최종 순위·표 ID·리랭커 입력 토큰 수가 모두 일치했다. 두 구현은 검증된 같은 원문·벡터와 같은 고정 모델 인스턴스를 사용했고 BM25와 검색 코드는 각각 실행했다. 기록: `artifacts/verification/table-rag/live-search-parity.json`. 고정 계획 재생 2건은 이 검색 비교와 별도로 계획·SQL·출처 조립 경계를 확인한다.

## Langfuse 게시와 재조회

8건의 참고답 평가에서 146개, 참고답 없는 평가에서 Faithfulness 1개를 게시했다. 같은 grade를 재게시한 뒤 **147/147 점수**를 원격 조회해 ID·값·게시 날짜를 확인했다. 점수 ID, 저장된 게시 timestamp, evaluation hash는 재게시 전후 모두 같았다. 재생성·재채점 없이 저장된 답변과 점수만 사용했다. 결과는 artifacts/verification/table-rag/republish-checks.json과 publication-snapshot-before.json에 남겼다.

trace 입출력 확인에는 Langfuse API의 io 필드를 명시해야 한다. 이를 빠뜨렸을 때의 초기 partial 결과도 로컬에 보존했고, 수정 후 8/8 실제 입출력을 확인했다. main의 HTTP 평가 연결에서도 bool 형태의 사용량 metadata를 읽고, 계획·답변 호출과 포함 SQL을 정상 복원했다.


## 최종 코드 검사

2026-11-12, 최종 검사 시 HEAD c59887e와 현재 작업 트리에서 수행했다. main 병합 기준은 63e7b71이며, 작업 중 추가된 별도 웹 변경 커밋도 보존했다. 아래 결과는 RAG·API·데이터·평가와 공통 Python 검사 범위다.

| 검사 | 결과 |
| --- | --- |
| Ruff 전체 | 통과 |
| 전체 pytest | 485 passed, 15 subtests passed |
| CLI 연결 | 10개 --help 통과 |
| 일반·보안 샘플 실행 | 모두 통과 |
| sdist·wheel 빌드 | 통과 |
| wheel 리소스 | 계획/답변/Judge 프롬프트·카탈로그·샘플 포함, experimental/tests 미포함 |
| uv lock --check | 168개 패키지 확인 |
| git diff --check | 통과 |
| 변경 문서 경로 링크 | 19개 문서, 누락 경로 없음 |

첫 전체 검사에서 기존 Qdrant/근거 주입 테스트가 새 TableRAG 기본 설정을 선택해 실패했다. 해당 테스트의 기존 응답·출처·오류·정리 assertion을 유지하고 legacy 경로를 명시했다. 관련 29개 및 6개 하위 검사와 독립 재검토를 거쳐 전체 검사를 다시 통과했다. 초기 실패 로그도 로컬에 보존한다.

데이터/지속성과 런타임/추적 경계를 별도 독립 리뷰했다. 캐시 조건, 완성 번들의 불변 처리, 동일 출력의 작성자 잠금, HTTP trace의 사용량·SQL 복원, 점수 조회의 지연 처리 지적을 수정했고 해당 재검토에서 추가 확정 지적은 없었다. 빠른 시작의 사용자 지정 번들 선택과 legacy 설정 범위도 별도로 점검했다.

재현:

```bash
uv run --locked python -m ruff check .
uv run --locked python -m pytest -q tests
uv run --locked python -m app.ingestion.scripts.prepare_table_rag check
uv run --locked python -m app.rag.cli --help
uv run --locked python -m app.evaluation.scripts.run_table_rag --help
uv run --locked python -m build
```

상세 검사 명령·종료 코드는 artifacts/verification/table-rag/final-checks.json, 빌드 리소스 검사는 wheel-check.json에 있다. 실제 배포나 CI 원격 실행은 이번 검사에 포함하지 않았으며 커밋·푸시는 하지 않았다.


## 원본에서의 전체 구축 완료

기존 SQLite·벡터·개인 실험 산출물을 입력으로 주지 않고 별도 작업/출력 경로에서 구축했다. 원본 Advanced PDF 120개와 설치된 고정 모델 가중치를 사용했다. 같은 호스트의 새 데이터 작업 경로를 검증한 것이며, OS와 Python 환경을 새로 설치한 검사는 아니다.

```bash
uv run --locked python -m app.ingestion.scripts.prepare_table_rag build   --documents data/advanced/documents   --output artifacts/.advanced-table-rag-cold-v2   --work-dir artifacts/.advanced-table-rag-cold-v2-work   --device cuda:0
uv run --locked python -m app.ingestion.scripts.prepare_table_rag check   --directory artifacts/.advanced-table-rag-cold-v2
```

| 확인 항목 | 결과 |
| --- | --- |
| 재사용한 DB/벡터 | 없음, reused=false, 재사용 임베딩 청크 0 |
| 원본 페이지 / 표 검색 단위 | 18,000 / 3,750 |
| 물리 표 / 표 행 | 21,126 / 100,889 |
| 대상 / 코드 키 / 속성 / 출처 연결 | 25,451 / 121,495 / 26,320 / 36,767 |
| 벡터 | float32, 21,750 × 2,048, 모든 값 유한 |
| 벡터 L2 norm | 0.99999988–1.00000012 |
| 임베딩 입력 절단 | 없음; 페이지 최대 1,316, 표 최대 878토큰 |
| 실제 GPU 임베딩 시간 | 977.4초 |
| DB·표·FTS·출처의 기존 데이터와 동등성 | 전체 행 대조 통과 |
| 번들 check / 코퍼스·RecordIndex 로드 | 통과 |
| 새 GPU 벡터의 CPU 실제 검색 | 표본 3건 모두 후보 20개와 순서가 기존 검증 번들과 일치 |

새 벡터는 기존 캐시와 바이트 단위로 같지 않다. 행별 코사인 유사도는 최솟값 0.9998125, 평균 0.9999713이었다. 같은 입력·모델 리비전의 재임베딩에서도 수치 차이가 있으므로 전체 질문 순위가 항상 같다고 일반화하지 않는다.

기본 실행은 실제 API·평가 검증을 마친 `artifacts/advanced-table-rag/`를 사용하며, 기존 번들과 과거 이력을 보존했다. 별도 raw 구축은 같은 제품 로더와 CPU 검색기로 검사했다. 전체 데이터셋에 대한 새 검색 품질 벤치마크나 운영 부하 검사는 이번 범위에 포함하지 않았다.

로컬 완료 근거:
- `artifacts/.advanced-table-rag-build/cold-v2-completion-summary.json`: 환경·입력·파이프라인 지문, 데이터와 벡터 검사.
- `artifacts/.advanced-table-rag-build/cold-v2-raw-parity-summary.json`: 전체 DB·표의 의미 비교.
- `artifacts/verification/table-rag/cold-runtime-check.json`: 새 번들의 CPU 검색 비교.
- `artifacts/verification/table-rag/final-score-readback.json`: 최종 코드로 147/147 원격 점수 확인.
- `artifacts/verification/table-rag/verified-content-fingerprints.json`: 최종 검사 대상 파일 지문.

계획의 데이터 구축·조회·답변·저장·추적·평가와 필수 검사를 완료했다. 남은 품질 한계는 위 실제 채점과 수치 재현성 범위에 기록했다.
