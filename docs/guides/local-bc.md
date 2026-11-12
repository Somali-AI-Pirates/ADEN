# Advanced 로컬 B·C 평가 연결

서버 API는 TableRAG 전용이다. 이 문서는 기존 B/C 검색·생성 경로를 평가 CLI에서 계속 사용하는 방법을 다룬다. B/C `Resources`나 `resources_loader`를 API에 주입하거나 `rag_backend`로 서버 경로를 선택하는 기능은 없다. [기본 빠른 시작](quickstart.md)

이 문서의 범위는 B/C 평가 어댑터의 고정 근거·생성·추적 연결과 C의 평가·보고서다. TableRAG 서버의 데이터 구축과 실행에는 이 문서의 근거 주입·Qdrant 절차를 적용하지 않는다. 평가 설정은 `configs/advanced.yaml`을 기본으로 하며 공통 문맥은 최대 8개·8,000토큰이다.

## A 없이 연결하기

`load_generation_resources(settings, evidence_provider=provider)`는 평가 어댑터용 생성 클라이언트·토큰 계산기·추적기만 준비한다. 임베더와 Qdrant는 만들지 않는다. `provider(question, mode)`는 `(list[RetrievedChunk], ContextBundle | None)`을 반환한다. A가 순위 후보만 넘기면 문맥 자리에 `None`을 주고 평가 서비스가 공통 `build_context`로 최종 문맥을 만든다. 검색 모드도 인자로 전달하므로 지원하지 않는 모드는 명시적으로 거부해야 한다.

최종 문맥까지 반환하면 다시 구성하지 않는다. 설정의 k·토큰 예산 초과는 평가 실행 기록에 오류로 남기며, 실제 본문·출처 ID·문맥 해시를 생성과 평가에 그대로 사용한다. 이 연결은 `ServiceBootstrap(resources_loader=...)`를 사용하는 B/C 평가 CLI용이다.

일반·보안 평가 어댑터는 같은 로더를 사용할 수 있다. 보안 비교는 문맥을 한 번 준비하고 base/defense에 같은 본문·출처를 사용한다. 정답 파일은 provider나 생성 입력에 넣지 않는다.

## B/C 고정 근거 평가 CLI

A 색인이 준비되기 전에는 평가 bootstrap에서 공통 공급기 `app.service.evidence:stored_evidence`를 사용한다. 이 경로는 저장된 후보와 최종 청크를 입력받으며 임베더·Qdrant를 만들지 않는다. 실제 모델 호출과 외부 추적에는 [공식 실행 기준](c-module-guide.md#팀-공식-실행)을 적용한다.

### 근거 JSONL 준비

`ADEN_EVIDENCE_RUNS`는 UTF-8 JSONL 파일 경로다(BOM도 허용). 한 줄에 질문·방식별 레코드 하나를 둔다. 생성 입력에 정답·채점 규칙은 넣지 않는다. 실제 근거와 실행 결과는 승인된 로컬 경로에 두며 개인 실험·PDF·.env를 제품 코드에 복사하지 않는다.

```json
{"question":"예약 DEMO의 접수 상태는 무엇인가요?","retrieval_mode":"naive","retrieved_candidates":[{"chunk_id":"DEMO-p1-c1","book_id":"DEMO","file_name":"DEMO.pdf","page":1,"heading":"예약 DEMO","text":"예약 DEMO는 접수 상태입니다. 운송 확정 기록은 없습니다.","rank":1,"score":1.0,"retriever":"fixed"}],"final_context_chunks":[{"chunk_id":"DEMO-p1-c1","book_id":"DEMO","file_name":"DEMO.pdf","page":1,"heading":"예약 DEMO","text":"예약 DEMO는 접수 상태입니다. 운송 확정 기록은 없습니다.","source_id":"S1"}]}
```

위 내용은 형식 설명용 가상 자료이며 실제 업무 질문의 근거가 아니다.

- `retrieved_candidates`에는 `Chunk` 필드와 `rank`·`score`·`retriever`를 넣는다. `heading`은 생략할 수 있다.
- 후보만 제공하면 `ADEN_EVIDENCE_CONTEXT`를 비운다. 서비스가 설정의 k·토큰 예산으로 문맥을 선택한다.
- `ADEN_EVIDENCE_CONTEXT=1`이면 `final_context_chunks`의 청크 ID 순서대로 후보에서 찾아 공통 문맥 형식으로 재구성한다. 자료 ID는 이 순서에 따라 S1부터 다시 부여한다.
- 고정 모드에서는 후보와 최종 청크의 본문·문서·쪽·제목이 같아야 한다. 누락된 후보 ID, 중복 최종 청크, k·토큰 한도로 저장 순서를 온전히 재현할 수 없는 자료는 평가 오류로 기록하고 모델을 호출하지 않는다.
- 이 변수는 기존 호환성을 위해 비어 있지 않은 문자열이면 고정 모드다. `0`으로 끄지 말고 빈 문자열을 쓴다.
- 파일에 저장된 질문 문자열과 요청 질문이 같아야 한다. 미등록 질문은 KeyError, 미등록 방식은 NotImplementedError로 평가 기록에 남기고 생성 호출을 거부한다. 다른 자료나 방식으로 임의 대체하지 않는다.

### 평가 CLI 실행

저장소 루트에서 실행한다. `.venv`와 의존성 설치는 [개발 가이드](development.md#설치)를 따른다. 실제 모델 평가에는 `OPENAI_API_KEY`를 로컬 `.env` 또는 환경변수로 제공한다. 키를 명령·문서에 적지 않는다.

```bash
uv run --locked python -m app.evaluation.scripts.run_benchmark \
  --adapter app.service.bootstrap:query_adapter \
  --config configs/advanced.yaml \
  --questions <질문 파일> --references <정답 파일> --ids <ID>
```

`ADEN_EVIDENCE_PROVIDER`, `ADEN_EVIDENCE_RUNS`, `ADEN_EVIDENCE_CONTEXT`를 설정하면 bootstrap이 같은 고정 근거 공급기를 사용한다. 평가 CLI는 서버 API의 `/query`를 경유하지 않으며 임의의 B/C 자원을 API 서버에 연결하지 않는다.

### 실행 기록과 추적 조회

평가 CLI는 `RunRecord`의 `request_id`와 설정·문맥 해시를 실행 결과에 남긴다. Langfuse를 사용할 때는 해당 ID로 trace와 모델 관측을 연결한다. API 응답의 `X-Request-ID`나 HTTP 요청 로그는 이 B/C 평가 adapter 실행 계약에 포함되지 않는다.

상세 추적에는 `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`·`LANGFUSE_BASE_URL`과 프로젝트 조회 권한이 필요하다. 수집 지연 시 재조회하며 flush 성공만으로 저장됐다고 판단하지 않는다.

| 관측 | 확인할 내용 |
| --- | --- |
| `rag-query` | 질문·방식, request_id·dataset_id·config_hash·prompt_hash, 최종 상태·답변·reason·출처·서비스 시간 |
| `retrieval` | 공급된 후보와 `provided_context=true` |
| `final-context` | 최종 청크·순서·본문·해시·토큰 수·예산 제외 청크 |
| `generation-attempt-N` | 실제 모델 입력·원시 출력·모델·출력 한도·추론 수준·사용량·시도별 시간·오류 |
| `validation-attempt-N` | 출력 형식·출처·지원 날짜 검증 구간 |

PR49 프롬프트 적용은 로컬 프롬프트 SHA-256와 root의 `prompt_hash`를 비교하고 실제 생성 입력의 system 본문까지 대조한다. `config_hash`는 설정 JSON과 프롬프트 해시를 결합하므로 프롬프트가 바뀌면 달라진다.

평가 프로세스를 정상 종료해 자원 정리와 추적 전송을 마친다. 강제 종료하면 마지막 관측이 유실될 수 있다. 추적 비활성화·전송 실패로 저장되지 않은 세부 기록은 평가 결과 파일만으로 복구할 수 없다.

### B/C 확인 범위

B는 평가 어댑터·고정 근거 연결·프롬프트 적용·RunRecord/trace 연결을 확인하고 재현 방법을 전달한다. C는 근거 충분/필수 규칙 누락/대체 근거 등의 평가, Judge 구조·기준, RAGAS를 맡는다. HTTP 시간 측정은 TableRAG API의 별도 평가 절차를 따른다. 고정 근거 연결 검증을 Advanced 실제 검색 방식 비교나 답변 품질 통과로 해석하지 않는다. 최종 B/C 검색 비교는 해당 평가용 색인과 실행 조건을 확인한 뒤 진행한다.

### B 작업 확인 순서

1. 실행 버전과 평가 설정을 고정한다.
2. 공통 저장 근거 공급기의 후보/고정 문맥 경로를 확인한다.
3. B/C 평가 CLI에서 생성과 실행 기록이 만들어지는지 확인한다.
4. trace의 실제 모델 입력과 프롬프트·설정 해시를 대조한다.
5. RunRecord·trace와 원격 문맥·출력·사용량을 대조한다.
6. 위 평가 안내와 JSONL 형식·기록 조회 방법을 C에 전달한다.
7. C가 생성 문제를 공유하면 해당 문맥·출력으로 재현하고 보완한다.

2026-11-11의 한 문항 실제 생성과 Langfuse 대조는 당시 API 통합 경로를 사용한 과거 검증 기록이다. 그 기록은 과거 실행의 결과이며 현재 서버의 B/C API 연결을 뜻하지 않는다. 근거 충분/부족 비교와 Judge·RAGAS 품질 평가는 수행하지 않았다. 실제 원문·답변·로그·개인 실험 결과는 저장소 밖에 보관한다.

## 메모리 Qdrant 평가 연결

B/C 평가 자원 로더는 기본적으로 `QdrantClient(location=":memory:")`를 사용하며 Cloud URL·키를 요구하지 않는다. 빈 메모리 클라이언트는 색인이 준비된 상태가 아니므로 평가 실행을 시작할 수 없다.

같은 평가 프로세스에서 적재한 클라이언트는 `load_resources(settings, qdrant_client=client)`로 전달한다. 자원 로더는 색인 이름·임베딩 차원을 확인하고 종료 때 클라이언트를 닫는다. `ADEN_QDRANT_CLIENT` 팩터리도 `ServiceBootstrap`이 평가 실행에서 사용한다. 별도 프로세스의 메모리 컬렉션은 공유되지 않는다.

메모리 색인은 프로세스가 끝나면 사라진다. 색인 생성 CLI(`build_index`)와 검색 전용 측정(`eval_retrieval`)은 현재도 Qdrant Cloud 키를 쓴다. 이 평가는 B/C 평가 bootstrap과 별도 색인을 사용할 수 있다.

## B/C 평가 CLI와 공통 bootstrap

`app.service.bootstrap`의 callable은 `load_resources(settings)`로 평가 자원을 준비한다. 설정과 자원 연결은 셸 또는 저장소 루트 `.env`에서 고른다. 같은 설정과 색인을 평가 CLI에 제공하면 데이터셋·`k`·문맥 예산·검색 입력을 재현할 수 있다. 이 자원 bootstrap은 TableRAG API 서버에서 사용하지 않는다.

| 환경변수 | 값 | 동작 |
| --- | --- | --- |
| `ADEN_CONFIG` | 설정 파일 경로(저장소 루트 기준). 없으면 `configs/advanced.yaml` | B/C 평가 bootstrap 설정이다. 평가 CLI의 `--config`는 그 실행에서 이 값보다 우선한다 |
| `ADEN_QDRANT_CLIENT` | `모듈:이름`. `settings`를 받아 색인을 적재한 `QdrantClient`를 반환하는 함수 | 그 색인으로 평가 검색을 실행한다. 자원 로더가 색인 이름·임베딩 차원을 확인하고 클라이언트 종료도 맡는다 |
| `ADEN_EVIDENCE_PROVIDER` | `모듈:이름`. `settings`를 받아 `evidence_provider`를 반환하는 함수 | 임베더·Qdrant 없이 평가에 근거를 주입한다 |

두 연결은 하나만 지정한다. 팩터리가 만든 자원은 반환된 `Resources`가 소유하며 `ServiceBootstrap.close()` 또는 프로세스 종료 때 닫는다. 준비에 실패한 경우에도 이미 받은 클라이언트는 닫는다.

평가 준비가 실패하면 첫 질문 전에 원인을 포함한 오류로 종료한다. TableRAG API의 readiness와 오류 동작은 [공통 HTTP 계약](../design/contracts.md#http)을 따른다.

실행 예시다. 메모리 색인은 프로세스마다 따로 있으므로 각 평가 실행이 자체 팩터리로 적재한다.

1. `.env`에 `OPENAI_API_KEY`와, 추적을 남기려면 Langfuse 키를 둔다.
2. 평가용 색인 적재 함수 또는 근거 공급 함수를 준비한다.
3. 같은 평가 설정으로 C 모듈의 CLI를 실행한다.

```bash
export ADEN_CONFIG=configs/basic.yaml
export ADEN_QDRANT_CLIENT=experimental.Damsebi.evidence_sources:memory_copy
python -m app.evaluation.scripts.run_benchmark --adapter app.service.bootstrap:query_adapter \
    --config configs/basic.yaml --questions <질문 파일> --references <정답 파일> --ids <ID> \
    --judge-model <모델> --ragas-model <모델>
```

두 평가 실행이 같은 구성을 썼는지는 trace의 `rag-query` metadata(`config_hash`, `dataset_id`)와 `final-context`의 `final_context_hash`로 대조한다.

### 검증 도구

[experimental/Damsebi/evidence_sources.py](../../experimental/Damsebi/evidence_sources.py)는 공통 코드가 아닌 개인 검증 도구다. 공통 코드는 이 모듈을 import하지 않고 환경변수에 적힌 이름으로만 불러온다. A의 적재 경로가 연결되면 `ADEN_QDRANT_CLIENT`를 그 함수로 바꾼다.

| 지정 | 용도 |
| --- | --- |
| `ADEN_QDRANT_CLIENT=experimental.Damsebi.evidence_sources:memory_copy` | Qdrant Cloud의 기존 컬렉션(벡터·payload) 전체를 메모리 Qdrant로 복사해 실제 검색을 실행한다. `.env`에 `QDRANT_URL`·`QDRANT_API_KEY`가 필요하다 |
| `ADEN_EVIDENCE_PROVIDER=app.service.evidence:stored_evidence`와 `ADEN_EVIDENCE_RUNS=<runs.jsonl>` | 이전 실행의 (질문, 검색 방식)별 후보를 그대로 넘긴다. 임베더·Qdrant 없이 B·C를 검증한다. 고정 근거 검증이며 검색 성능 비교가 아니다 |
| 위에 `ADEN_EVIDENCE_CONTEXT=1` 추가 | 최종 문맥까지 넘겨 서비스가 문맥을 다시 고르지 않는 경로를 확인한다 |

저장되지 않은 검색 방식은 NotImplementedError, 저장되지 않은 질문은 KeyError로 평가 기록에 남는다. 고정 근거 공급기는 `app/service/evidence.py`의 공통 코드이며, `experimental.Damsebi.evidence_sources`의 기존 import는 호환용으로 유지한다.

## 로컬 확인

프로젝트 환경에서 실행한다.

```powershell
python -m pytest -q tests/service/test_prepared_context.py tests/generation/test_generation.py
python -m pytest -q tests
python -m ruff check .
python -m app.evaluation.scripts.run_benchmark --sample --publish-langfuse-scores --experiment-id sample-bc-local
```

첫 테스트는 실제 서비스와 LangChain 체인을 사용하되 모델·외부 추적만 모의 객체로 대체한다. 메모리 Qdrant 연결 검사는 실제 QdrantLocal을 사용한다. `--sample` 평가·점수 게시는 모두 가상이며 실제 Advanced 검색·답변 품질이나 Langfuse 서버 수신을 입증하지 않는다. 샘플을 다시 실행할 때는 새 실험 ID를 사용한다.

실제 생성은 OpenAI API를 사용한다. Langfuse는 환경변수의 서버로 연결되며 로컬 서버를 쓰면 그 주소를 `LANGFUSE_BASE_URL`에 지정한다. 키 값은 로그·보고서에 쓰지 않는다.

## 추적과 평가

검색·최종 문맥·생성 시도는 실제 실행 구간을 감싼다. 제공된 고정 문맥을 사용하는 경우 `provided_context=true`를 남기며 그 조회 시간은 과거 A 실행 시간이 아니다. 모델 시도별 `latency_ms`는 체인 호출 시간이며 서비스·HTTP 전체 시간과 구분한다.

`final-context`에는 실제 문맥 토큰 수와 `budget_excluded_chunk_ids`를 기록한다. 이 목록은 토큰 예산으로 제외한 청크만 뜻하며 k 제한이나 선택 정책에 의한 탈락과 구분한다.

평가에서는 정답을 실행 후에만 읽어 후보/최종 문맥의 근거 보존을 계산한다. Judge·RAGAS·검색 진단 점수는 `--publish-langfuse-scores`를 명시했을 때만 trace에 게시한다. 미평가·해당 없음은 0점으로 보내지 않는다. 게시 실패도 보고서에 남기며 기존 답변·점수 파일을 버리지 않는다.

실제 모델·Langfuse 서버·RAGAS 연결은 2026-11-10에 Basic 1문항으로 확인했다. 관측의 상하 관계와 실제 구간, 점수 수신, 대표 trace 링크는 [통합 검증 기록](../../experimental/Damsebi/pr40-integration-2026-11-10.md)에, 공통 실행 경로의 API·평가 CLI 대조와 평가 세트별 점수 게시는 [공통 경로 검증 기록](../../experimental/Damsebi/common-path-2026-11-10.md)에 있다. Advanced 색인을 쓴 실행과 공식 세 방식 비교는 아직 별도 실행·검증이 필요하다.

## 제공 문맥의 일치 검사

최종 `ContextBundle`을 직접 반환할 때는 `build_context`로 구성한 자료 블록 형식과 현재 서비스의 토큰 계산기를 사용한다. B는 본문·최종 청크·자료 ID별 문서·페이지·SHA-256·토큰 수가 일치하는지 모델 호출 전에 검사한다. 불일치하면 일반 평가 어댑터는 오류 기록으로 중단하고 보안 비교의 검색 준비도 실패한다. 자료를 임의로 재정렬하거나 수정해 통과시키지 않는다. 후보만 반환하는 `(candidates, None)` 경로에서는 서비스가 같은 계산기로 문맥을 구성한다.
