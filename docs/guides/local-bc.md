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

이미 같은 실행에서 적재한 클라이언트는 `load_resources(settings, qdrant_client=client)`로 전달한다. 이 경우 자원 로더가 색인 이름·임베딩 차원을 확인하고 클라이언트 종료도 맡는다. A를 연결할 때 기존 벡터와 payload를 이 클라이언트에 적재해야 한다. 색인 생성 CLI를 별도 프로세스로 실행해 만든 메모리 컬렉션은 API에서 공유되지 않는다.

메모리 색인은 프로세스가 끝나면 사라진다. 로컬 실행은 worker 1개로 시작하고, 재시작 시 보관한 벡터·메타데이터를 재적재한다. 색인 생성 CLI(`build_index`)와 검색 전용 측정(`eval_retrieval`)은 아직 Qdrant Cloud 키를 쓰며 서비스 자원 로더와 색인을 공유하지 않는다. A를 이식할 때 적재 경로와 함께 맞춘다.

## 평가 CLI에 연결

평가 CLI는 `--adapter 모듈:이름`을 import해 호출한다. 표준 `app.service.bootstrap:query_adapter`는 빈 메모리 색인으로 시작해 모든 요청이 503이 되므로, 근거를 공급하는 로더로 `ServiceBootstrap`을 만든 모듈을 지정한다.

```python
from app.service.bootstrap import ServiceBootstrap, _QueryAdapter
from app.service.resources import load_generation_resources

_bootstrap = ServiceBootstrap(resources_loader=lambda settings: load_generation_resources(
    settings, evidence_provider=provider))
query_adapter = _QueryAdapter(_bootstrap)        # --adapter 대상
configure_evaluation = _bootstrap.configure      # --config 전달 훅
```

동작하는 예는 [experimental/Damsebi/bc_adapter.py](../../experimental/Damsebi/bc_adapter.py)다. 공통 코드가 아니라 개인 도구이며 A의 적재 경로가 연결되면 필요 없어진다.

| 근거 공급 | 지정 | 용도 |
| --- | --- | --- |
| 저장 후보 재생 | `ADEN_EVIDENCE_RUNS=<runs.jsonl>` | 이전 실행의 (질문, 검색 방식)별 후보를 그대로 넘긴다. 임베더·Qdrant 없이 B·C를 검증한다. 검색 성능 비교가 아니다 |
| 최종 문맥까지 주입 | 위에 `ADEN_EVIDENCE_CONTEXT=1` 추가 | 서비스가 문맥을 다시 고르지 않는 경로를 확인한다 |
| 메모리 색인 실제 검색 | 둘 다 없음 | Qdrant Cloud의 기존 컬렉션(벡터·payload)을 메모리 Qdrant로 복사해 `load_resources(settings, qdrant_client=...)`로 넘긴다. `.env`에 `QDRANT_URL`·`QDRANT_API_KEY`가 필요하다 |

```bash
ADEN_EVIDENCE_RUNS=artifacts/experiments/<원본 ID>/runs.jsonl \
python -m app.evaluation.scripts.run_benchmark --adapter experimental.Damsebi.bc_adapter:query_adapter \
    --config configs/basic.yaml --questions <질문 파일> --references <정답 파일> --ids <ID> \
    --judge-model <모델> --ragas-model <모델> --publish-langfuse-scores
```

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

실제 모델·Langfuse 서버·RAGAS 연결은 2026-11-10에 Basic 1문항으로 확인했다. 관측의 상하 관계와 실제 구간, 점수 수신, 재게시 중복 여부, 대표 trace 링크는 [통합 검증 기록](../../experimental/Damsebi/pr40-integration-2026-11-10.md)에 있다. Advanced 색인을 쓴 실행과 공식 세 방식 비교는 아직 별도 실행·검증이 필요하다.
