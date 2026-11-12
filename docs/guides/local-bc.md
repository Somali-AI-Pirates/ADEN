# Advanced 로컬 B·C 개발

일반 서버의 기본값은 DB 우선 통합 TableRAG다. 이 문서는 기존 B·C 경로를 대상으로 하며, 직접 서버를 실행할 때 설정의 rag_backend를 legacy로 지정한다. create_app에 resources_loader를 전달하는 예제는 해당 로더를 사용한다. [기본 빠른 시작](quickstart.md)

이 문서의 범위는 레거시 B의 생성·검증·추적과 C의 평가·보고서다. 새 기본 TableRAG 서버의 데이터 구축과 실행에는 이 문서의 근거 주입·Qdrant 절차를 적용하지 않는다. 설정 기본값은 `configs/advanced.yaml`이며 최종 최대 8개·8,000토큰을 사용한다.

## A 없이 연결하기

`load_generation_resources(settings, evidence_provider=provider)`는 OpenAI 생성 클라이언트·토큰 계산기·추적기만 준비한다. 임베더와 Qdrant는 만들지 않는다. `provider(question, mode)`는 `(list[RetrievedChunk], ContextBundle | None)`을 반환한다. A가 순위 후보만 넘기면 문맥 자리에 `None`을 주고, 서비스가 공통 `build_context`(설정의 k·예산·토큰 계산기)로 최종 문맥을 만든다. 검색 모드도 인자로 전달하므로, 지원하지 않는 모드는 명시적으로 거부해야 한다.

최종 문맥까지 반환하면 다시 구성하지 않는다. 설정의 k·토큰 예산 초과는 `503` 오류로 처리하며, 실제 본문·출처 ID·문맥 해시를 생성과 평가에 그대로 사용한다. API 요청 본문으로 임의 문맥을 받는 기능은 추가하지 않았다.

FastAPI는 기존 앱 팩터리에 로더를 넣는다.

```python
from app.api.main import create_app
from app.service.resources import load_generation_resources

# provider는 실행 코드가 준비한 후보와 ContextBundle을 반환하는 함수다.
app = create_app(resources_loader=lambda settings: load_generation_resources(
    settings, evidence_provider=provider,
))
```

일반·보안 평가에는 기존 `ServiceBootstrap(resources_loader=...)`에 같은 로더를 전달한다. 보안 비교는 문맥을 한 번 준비하고 base/defense에 같은 본문·출처를 사용한다. 정답 파일은 provider나 생성 입력에 넣지 않는다.


## B/C 고정 근거 API 실행

A 색인이 준비되기 전에는 공통 공급기 `app.service.evidence:stored_evidence`를 사용한다. 이 경로는 저장된 후보와 최종 청크를 입력받으며 임베더·Qdrant를 만들지 않는다. 실제 모델 호출과 외부 추적에는 [공식 실행 기준](c-module-guide.md#팀-공식-실행)을 적용한다.

### 근거 JSONL 준비

`ADEN_EVIDENCE_RUNS`는 UTF-8 JSONL 파일 경로다(BOM도 허용). 한 줄에 질문·방식별 레코드 하나를 둔다. 생성 입력에 정답·채점 규칙은 넣지 않는다. 실제 근거와 실행 결과는 승인된 로컬 경로에 두며 개인 실험·PDF·.env를 제품 코드에 복사하지 않는다.

```json
{"question":"예약 DEMO의 접수 상태는 무엇인가요?","retrieval_mode":"naive","retrieved_candidates":[{"chunk_id":"DEMO-p1-c1","book_id":"DEMO","file_name":"DEMO.pdf","page":1,"heading":"예약 DEMO","text":"예약 DEMO는 접수 상태입니다. 운송 확정 기록은 없습니다.","rank":1,"score":1.0,"retriever":"fixed"}],"final_context_chunks":[{"chunk_id":"DEMO-p1-c1","book_id":"DEMO","file_name":"DEMO.pdf","page":1,"heading":"예약 DEMO","text":"예약 DEMO는 접수 상태입니다. 운송 확정 기록은 없습니다.","source_id":"S1"}]}
```

위 내용은 형식 설명용 가상 자료이며 실제 업무 질문의 근거가 아니다.

- `retrieved_candidates`에는 `Chunk` 필드와 `rank`·`score`·`retriever`를 넣는다. `heading`은 생략할 수 있다.
- 후보만 제공하면 `ADEN_EVIDENCE_CONTEXT`를 비운다. 서비스가 설정의 k·토큰 예산으로 문맥을 선택한다.
- `ADEN_EVIDENCE_CONTEXT=1`이면 `final_context_chunks`의 청크 ID 순서대로 후보에서 찾아 공통 문맥 형식으로 재구성한다. 자료 ID는 이 순서에 따라 S1부터 다시 부여한다.
- 고정 모드에서는 후보와 최종 청크의 본문·문서·쪽·제목이 같아야 한다. 누락된 후보 ID, 중복 최종 청크, k·토큰 한도로 저장 순서를 온전히 재현할 수 없는 자료는 503으로 거부하며 모델을 호출하지 않는다.
- 이 변수는 기존 호환성을 위해 비어 있지 않은 문자열이면 고정 모드다. `0`으로 끄지 말고 빈 문자열을 쓴다.
- 파일에 저장된 질문 문자열과 요청 질문이 같아야 한다. 미등록 질문은 503, 미등록 방식은 501이다. 다른 자료나 방식으로 임의 대체하지 않는다.

### 서버 기동과 HTTP 요청

저장소 루트의 PowerShell에서 실행한다. `.venv`와 의존성 설치는 [개발 가이드](development.md#설치)를 따른다. `OPENAI_API_KEY`는 `.env` 또는 환경변수로 제공하며 명령·문서에 키 값을 적지 않는다.

```powershell
$env:ADEN_CONFIG = 'configs/advanced.yaml'
$env:ADEN_QDRANT_CLIENT = ''
$env:ADEN_EVIDENCE_PROVIDER = 'app.service.evidence:stored_evidence'
$env:ADEN_EVIDENCE_RUNS = 'C:\bc-inputs\runs.jsonl' # 준비한 파일의 실제 경로
$env:ADEN_EVIDENCE_CONTEXT = '1'
.\.venv\Scripts\python.exe -B -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

다른 터미널에서 상태를 확인한 뒤 질문한다. `/health`는 모델을 호출하지 않고 `/query`는 실제 생성 요청이다.

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health'
$payload = @{ question = '예약 DEMO의 접수 상태는 무엇인가요?'; retrieval_mode = 'naive' } | ConvertTo-Json
$response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/query' -Method Post `
    -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload))
$body = $response.Content | ConvertFrom-Json
$body.request_id
$response.Headers['X-Request-ID']
```

`/health`의 `dataset=advanced`, `query_ready=true`를 확인한다. 준비에 실패하면 `/query`의 503 `reason`과 연결 변수·파일 형식을 확인한다. API 본문은 기존 `question`·`retrieval_mode`를 유지하며, 문맥을 직접 받는 필드는 추가하지 않는다.

PR #49의 생성 경로는 `app.service.query.answer_query` → `app.generation.chain.generate_answer`이며 `app/generation/prompts/shipping_qa.txt`를 읽는다. 현재 Advanced 설정은 gpt-6-luna, low, 출력 한도 4096, 최대 생성 시도 2회, 모델 호출 제한 60초다. 클라이언트 제한 시간은 재시도도 고려한다. 실행 버전은 `git rev-parse HEAD`, 생성 설정은 설정 파일, 실제 전송 값은 trace로 확인한다.

### 요청 ID와 실행 기록 조회

응답 JSON의 `request_id`와 `X-Request-ID`는 같다. 서버의 `http_request request_id=...` 요약 로그에는 method·path·status_code·latency_ms가 있고, 상세 문맥·시도·출력의 RunRecord 파일은 API가 자동 생성하지 않는다.

상세 연결에는 Langfuse의 `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`·`LANGFUSE_BASE_URL`과 프로젝트 조회 권한이 필요하다. 추적을 사용할 때는 `LANGFUSE_TRACING_ENABLED=false` 또는 `OTEL_SDK_DISABLED=true`가 남아 있지 않은지 확인한다. 요청 ID로 trace ID를 계산해 기존 SDK로 조회한다.

```python
from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv()  # 저장소 루트에서 실행하며 키 값은 출력하지 않는다.
client = Langfuse()
try:
    trace_id = client.create_trace_id(seed="<응답 request_id>")
    observations = client.api.observations.get_many(
        trace_id=trace_id, limit=50,
        fields="core,basic,time,io,metadata,model,usage",
    ).data
finally:
    client.shutdown()
```

`io`·`metadata`·`model`·`usage`를 포함해야 실제 입력·출력·설정·사용량을 읽을 수 있다. 수집 지연 시 재조회하며, flush 성공만으로 저장됐다고 판단하지 않는다.

| 관측 | 확인할 내용 |
| --- | --- |
| `rag-query` | 질문·방식, request_id·dataset_id·config_hash·prompt_hash, 최종 상태·답변·reason·출처·서비스 시간 |
| `retrieval` | 공급된 후보와 `provided_context=true` |
| `final-context` | 최종 청크·순서·본문·해시·토큰 수·예산 제외 청크 |
| `generation-attempt-N` | 실제 모델 입력·원시 출력·모델·출력 한도·추론 수준·사용량·시도별 시간·오류 |
| `validation-attempt-N` | 출력 형식·출처·지원 날짜 검증 구간 |

PR49 프롬프트 적용은 로컬 프롬프트 SHA-256와 root의 `prompt_hash`를 비교하고 실제 생성 입력의 system 본문까지 대조한다. `config_hash`는 설정 JSON과 프롬프트 해시를 결합하므로 프롬프트가 바뀌면 달라진다.

서버는 Ctrl+C로 정상 종료해 자원 정리와 추적 전송을 마친다. Windows에서 강제 종료하면 마지막 관측이 유실될 수 있다. 추적 비활성화·전송 실패로 저장되지 않은 세부 기록은 API 응답과 요약 로그만으로 복구할 수 없다. 이 경우 로컬 상세 기록 저장이 필요한지는 별도 협의한다.

### B/C 확인 범위

B는 실행·고정 근거 연결·프롬프트 적용·요청/trace 연결을 확인하고 재현 방법을 전달한다. 생성 문제가 공유되면 그 입력 문맥·출력으로 재현해 보완한다. C는 근거 충분/필수 규칙 누락/대체 근거 등의 평가, Judge 구조·기준, RAGAS, HTTP 클라이언트와 응답 시간 측정을 맡는다.

HTTP 시간은 C가 실제 요청 시작부터 응답 수신까지 측정하고 같은 request_id의 품질 결과에 연결한다. 서비스·생성 시도·Judge·RAGAS 시간은 별도로 남긴다. 고정 근거 연결 검증을 Advanced 실제 검색 방식 비교나 답변 품질 통과로 해석하지 않는다. 최종 검색 방식 비교는 A 색인 준비 후 진행한다.

### B 작업 확인 순서

1. 실행 버전과 Advanced 설정을 고정한다.
2. 공통 저장 근거 공급기의 후보/고정 문맥 경로를 확인한다.
3. 실제 HTTP /query로 생성되는지 확인한다.
4. trace의 실제 모델 입력과 프롬프트·설정 해시를 대조한다.
5. 응답 request_id·요약 로그·trace와 원격 문맥·출력·사용량을 대조한다.
6. 위 실행 안내와 JSONL 형식·기록 조회 방법을 C에 전달한다.
7. C가 생성 문제를 공유하면 해당 문맥·출력으로 재현하고 보완한다.

2026-11-11에 이 공통 경로로 고정 근거 Advanced 1문항을 실제 생성해 HTTP 200 응답과 Langfuse 원격 문맥·생성 입력·출력·사용량의 일치를 확인했다. 근거 충분/부족 비교와 Judge·RAGAS 품질 평가는 수행하지 않았다. 실제 원문·답변·로그·개인 실험 결과는 저장소 밖에 보관한다.

## 메모리 Qdrant 연결

기본 검색 자원 로더는 `QdrantClient(location=":memory:")`를 사용하며 Cloud URL·키를 요구하지 않는다. 빈 메모리 클라이언트는 색인이 준비된 상태가 아니므로 API는 503을 반환한다.

이미 같은 실행에서 적재한 클라이언트는 `load_resources(settings, qdrant_client=client)`로 전달한다. 이 경우 자원 로더가 색인 이름·임베딩 차원을 확인하고 클라이언트 종료도 맡는다. 적재 함수를 `ADEN_QDRANT_CLIENT`로 지정하면 API와 평가 CLI가 같은 방식으로 받는다([공통 실행](#api와-평가-cli의-공통-실행)). A를 연결할 때 기존 벡터와 payload를 이 클라이언트에 적재해야 한다. 색인 생성 CLI를 별도 프로세스로 실행해 만든 메모리 컬렉션은 API에서 공유되지 않는다.

메모리 색인은 프로세스가 끝나면 사라진다. 로컬 실행은 worker 1개로 시작하고, 재시작 시 보관한 벡터·메타데이터를 재적재한다. 색인 생성 CLI(`build_index`)와 검색 전용 측정(`eval_retrieval`)은 아직 Qdrant Cloud 키를 쓰며 서비스 자원 로더와 색인을 공유하지 않는다. A를 이식할 때 적재 경로와 함께 맞춘다.

## API와 평가 CLI의 공통 실행

API(`app.api.main:app`)와 평가 CLI(`app.service.bootstrap`의 callable)는 같은 `load_resources(settings)`로 자원을 준비한다. 설정과 자원 연결은 환경변수(셸 또는 저장소 루트 `.env`)로 고른다. 두 실행에 같은 값을 주면 같은 데이터셋·`k`·문맥 예산·색인으로 실행된다.

| 환경변수 | 값 | 동작 |
| --- | --- | --- |
| `ADEN_CONFIG` | 설정 파일 경로(저장소 루트 기준). 없으면 `configs/advanced.yaml` | API와 bootstrap의 실행 설정이다. 평가 CLI의 `--config`는 그 실행에서 이 값보다 우선하므로 같은 파일을 지정한다 |
| `ADEN_QDRANT_CLIENT` | `모듈:이름`. `settings`를 받아 색인을 적재한 `QdrantClient`를 반환하는 함수 | 그 색인으로 실제 검색·생성·평가를 실행한다. 자원 로더가 색인 이름·임베딩 차원을 확인하고 클라이언트 종료도 맡는다 |
| `ADEN_EVIDENCE_PROVIDER` | `모듈:이름`. `settings`를 받아 `evidence_provider`를 반환하는 함수 | 임베더·Qdrant 없이 근거 주입 경로를 실행한다 |

두 연결은 하나만 지정한다. 팩터리가 만든 자원은 반환된 `Resources`가 소유하며 API 종료나 `ServiceBootstrap.close()`(프로세스 종료 시 자동 호출)에서 닫는다. 준비에 실패한 경우에도 이미 받은 클라이언트는 닫는다.

준비에 실패하면 원인을 알린다. API는 `/health`의 `query_ready=false`와 `/query`의 `503` 응답 `reason`으로, 평가 CLI는 첫 질문 전에 같은 문구의 오류로 멈춘다.

| 원인 | 안내 |
| --- | --- |
| 연결 지정이 없거나 컬렉션이 없음 | 설정의 컬렉션 이름과 연결할 환경변수 이름 |
| `모듈:이름`을 불러오지 못하거나 팩터리가 실패 | 환경변수 이름, 지정값, 오류 종류. 오류 본문은 경로·주소가 들어갈 수 있어 넣지 않는다 |
| 두 연결을 함께 지정, 팩터리가 대상을 반환하지 않음 | 해당 환경변수 이름 |
| 색인과 임베딩의 차원 불일치 | 차원 확인 안내 |
| `ADEN_CONFIG` 파일이 없거나 형식 오류 | 시작 단계에서 파일 경로나 검증 오류로 멈춘다 |

실행 절차다. 메모리 색인은 프로세스마다 따로 있으므로 API와 평가 CLI가 각자 같은 팩터리로 적재한다.

1. `.env`에 `OPENAI_API_KEY`와, 추적을 남기려면 Langfuse 키를 둔다.
2. 색인 적재 함수(또는 근거 공급 함수)를 준비한다. A의 적재 함수가 연결되기 전에는 아래 검증 도구를 쓴다.
3. 같은 환경변수로 API와 평가를 실행한다.

```bash
export ADEN_CONFIG=configs/basic.yaml
export ADEN_QDRANT_CLIENT=experimental.Damsebi.evidence_sources:memory_copy

fastapi run --host 127.0.0.1    # GET /health의 dataset·query_ready 확인 후 POST /query
python -m app.evaluation.scripts.run_benchmark --adapter app.service.bootstrap:query_adapter \
    --config configs/basic.yaml --questions <질문 파일> --references <정답 파일> --ids <ID> \
    --judge-model <모델> --ragas-model <모델> --publish-langfuse-scores
```

두 실행이 같은 구성을 썼는지는 trace의 `rag-query` metadata(`config_hash`, `dataset_id`)와 `final-context`의 `final_context_hash`로 대조한다. 같은 색인·설정이면 후보 순서와 최종 문맥 해시가 같다.

### 검증 도구

[experimental/Damsebi/evidence_sources.py](../../experimental/Damsebi/evidence_sources.py)는 공통 코드가 아닌 개인 검증 도구다. 공통 코드는 이 모듈을 import하지 않고 환경변수에 적힌 이름으로만 불러온다. A의 적재 경로가 연결되면 `ADEN_QDRANT_CLIENT`를 그 함수로 바꾼다.

| 지정 | 용도 |
| --- | --- |
| `ADEN_QDRANT_CLIENT=experimental.Damsebi.evidence_sources:memory_copy` | Qdrant Cloud의 기존 컬렉션(벡터·payload) 전체를 메모리 Qdrant로 복사해 실제 검색을 실행한다. `.env`에 `QDRANT_URL`·`QDRANT_API_KEY`가 필요하다 |
| `ADEN_EVIDENCE_PROVIDER=app.service.evidence:stored_evidence`와 `ADEN_EVIDENCE_RUNS=<runs.jsonl>` | 이전 실행의 (질문, 검색 방식)별 후보를 그대로 넘긴다. 임베더·Qdrant 없이 B·C를 검증한다. 고정 근거 검증이며 검색 성능 비교가 아니다 |
| 위에 `ADEN_EVIDENCE_CONTEXT=1` 추가 | 최종 문맥까지 넘겨 서비스가 문맥을 다시 고르지 않는 경로를 확인한다 |

저장되지 않은 검색 방식은 `NotImplementedError`로 거부해 501 오류로 기록되고, 저장되지 않은 질문은 503 오류로 기록된다. 고정 근거 공급기는 `app/service/evidence.py`의 공통 코드이며, `experimental.Damsebi.evidence_sources`의 기존 import는 호환용으로 유지한다.

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

최종 `ContextBundle`을 직접 반환할 때는 `build_context`로 구성한 자료 블록 형식과 현재 서비스의 토큰 계산기를 사용한다. B는 본문·최종 청크·자료 ID별 문서·페이지·SHA-256·토큰 수가 일치하는지 모델 호출 전에 검사한다. 불일치하면 API와 일반 어댑터는 오류(`503`)로 중단하고 보안 비교의 검색 준비도 실패한다. 자료를 임의로 재정렬하거나 수정해 통과시키지 않는다. 후보만 반환하는 `(candidates, None)` 경로에서는 서비스가 같은 계산기로 문맥을 구성한다.
