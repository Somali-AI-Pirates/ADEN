# Advanced 로컬 B·C 개발

현재 범위는 B의 생성·검증·추적과 C의 평가·보고서다. 최신 A 검색 이식, 실제 Advanced 색인 적재와 실제 품질 평가는 후속 작업이다. 설정 기본값은 `configs/advanced.yaml`이며 최종 최대 8개·8,000토큰을 사용한다.

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
| `ADEN_EVIDENCE_PROVIDER=experimental.Damsebi.evidence_sources:stored_evidence`와 `ADEN_EVIDENCE_RUNS=<runs.jsonl>` | 이전 실행의 (질문, 검색 방식)별 후보를 그대로 넘긴다. 임베더·Qdrant 없이 B·C를 검증한다. 고정 근거 검증이며 검색 성능 비교가 아니다 |
| 위에 `ADEN_EVIDENCE_CONTEXT=1` 추가 | 최종 문맥까지 넘겨 서비스가 문맥을 다시 고르지 않는 경로를 확인한다 |

저장되지 않은 검색 방식은 `NotImplementedError`로 거부해 501 오류로 기록되고, 저장되지 않은 질문은 503 오류로 기록된다.

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
