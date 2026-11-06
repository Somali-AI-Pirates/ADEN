# 실행 기록·평가·결과 비교 가이드

작성일: 2026-11-05 · 수정일: 2026-11-06. 기준 문서: [공통 입출력·호출 계약](../design/contracts.md) · [시스템 설계](../design/architecture.md) · [개발 가이드](development.md) · [공통 협업 규칙](../../CONTRIBUTING.md)

필드·함수 시그니처의 기준은 [공통 계약](../design/contracts.md)이다. 이 문서는 `app/observability/`·`app/evaluation/` 코드의 사용법과 계약 검증 방식을 설명한다.

## 1. 구현 상태

| 구분 | 상태 |
| --- | --- |
| 실행 기록 검증·정규화, Langfuse 추적, Hit/Recall, Faithfulness·Judge 처리 규칙, 평가기·보안 비교 | 구현. 가상 샘플·모의 인프라 기반 테스트 통과 |
| 가상 인프라 통합·샘플 테스트 | 가짜 자원으로 `app.retrieval`→B 어댑터→C 기록 정규화와 고정 문맥 보안 비교를 확인. 실제 Basic 자료·색인·모델·Langfuse 서버 검증은 아님 |
| 실제 Langfuse 서버 저장 | **미검증.** 실제 서버에서 trace 저장·조회하지 않음 |
| 실제 RAGAS·Judge 평가 | **미검증.** RAGAS 의존성 호환성을 확인하거나 실제 평가 모델을 호출하지 않음 |
| 실제 색인·Basic 자료·생성 모델로 서비스 전체 통합 | **미실행.** 가상 인프라 테스트와 구분한다 |

공통 앱 타입은 `shared/contracts.py`에 정의한다. 실행 기록 필드와 변환 규칙은 [공통 계약 3절](../design/contracts.md#3-c가-받는-직렬화된-실행-기록)을 기준으로 하며, C는 B 연결부가 평탄화한 JSON/Mapping 로그를 `app/observability/records.py`에서 검증한다.

## 2. 파일

```text
app/
├── observability/      # records.py, tracing.py
├── evaluation/
│   ├── dataset.py, metrics.py, graders.py, runner.py, security.py, report.py
│   ├── prompts/        # judge.txt, security_judge.txt
│   ├── samples/        # 공용 가상 샘플
│   └── scripts/        # eval_retrieval.py, run_benchmark.py, run_security.py
└── generation/prompts/ # shipping_qa.txt, defense.txt
tests/
├── evaluation/
├── integration/
└── test_package_layout.py
```

## 3. 설치·실행

2026-11-05 과거 검사 메모에는 Python 3.13, `langfuse==5.1.0`, `ragas==0.4.3`, `openai==3.3.0`, `pytest==9.2.0`이 기록되어 있다. 이 버전 목록은 과거 검사 기록이며 현재 설치 안내가 아니다. 특히 현재 `pyproject.toml`은 `openai>=1.50,<3`으로 제한하므로 과거의 `openai==3.3.0`을 현재 실행과 혼용하지 않는다. 현재 공통 개발·테스트 설치는 저장소 루트에서 실행한다. RAGAS는 현재 의존성에 포함되지 않고, 설치·버전 고정 절차와 실제 평가 모델 호환성도 검증되지 않았다. 따라서 아래 `--ragas-model` 예시는 현재 기본 설치만으로 실행이 보장되는 명령이 아니다.

```bash
python -m pip install -e '.[test]'
```

모든 명령은 저장소 루트에서 모듈 방식으로 실행한다. 테스트와 가상 샘플 실행에는 API 키, 실제 색인, 실제 모델 호출이 필요 없다.

```bash
python -m pytest tests
```

```bash
python -m app.evaluation.scripts.run_benchmark --sample --experiment-id sample-guide-benchmark
```

```bash
python -m app.evaluation.scripts.run_security --sample --experiment-id sample-guide-security
```

샘플 결과는 `artifacts/experiments/sample-*/`와 `artifacts/sample-reports/sample-*/`에만 생성되며 Git에서 제외된다. 모든 샘플 결과에 "가상 샘플" 표시가 붙고 `data_kind=SAMPLE`이다. 샘플 실험 ID는 `sample-`로 시작해야 하며 실제 결과와 같은 실험 ID에 섞을 수 없다.

환경변수(비밀값은 로컬 `.env`·프로세스 환경 변수·플랫폼 비밀 설정으로 주입하며 로그·결과 파일에 남기지 않음):

| 변수 | 용도 |
| --- | --- |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | 없으면 Tracer가 `disabled` |
| `LANGFUSE_BASE_URL` | Langfuse 서버 주소(기본 cloud) |
| `OPENAI_API_KEY`, 필요 시 `OPENAI_BASE_URL` | Judge·RAGAS 평가 모델 호출 |

## 4. 서비스 연결 방법

### 4.1 Langfuse 추적

B는 리소스를 초기화할 때 `Resources.tracer`에 이 모듈의 Tracer를 연결하고, 요청마다 동기 호출한다. 현재 `app/service/resources.py`와 `app/service/query.py`가 이 연결을 사용한다. Tracer는 모듈 import 시 초기화되지 않는다.

```python
from app.observability import Tracer, compute_context_hash

tracer = Tracer.from_env()          # load_resources에서 한 번. import 시 네트워크 연결 없음

trace = tracer.start_request(request_id, question=q, retrieval_mode=mode,
                             dataset_id=settings.dataset, config_hash=config_hash)
trace.record_retrieval(retrieved_candidates=flat_candidates,         # 평탄 로그 청크
                       final_context_chunks=flat_final,
                       final_context_hash=compute_context_hash(flat_final),
                       k=settings.retrieval.k,
                       model_context_text=context.text,             # ContextBundle.text
                       model_context_hash=context.context_hash)
for attempt in attempt_records:                                     # 재시도·재생성 포함 모든 시도
    trace.record_attempt(**attempt)                                 # AttemptRecord 그대로
langfuse = trace.finish(status=status, answer=answer, reason=reason, citations=citations,
                        latency_ms=latency_ms, error_type=error_type)
# langfuse == {"langfuse_trace_id": ..., "langfuse_status": ..., "langfuse_errors": [...]}

tracer.shutdown()                   # Resources.close()에서 전송
```

- trace ID는 `Langfuse.create_trace_id(seed=request_id)`로 만든다. 같은 request_id로 trace를 다시 계산할 수 있다.
- trace 구조: `rag-query`(질문·최종 답변) → `retrieval`(검색 후보) → `final-context`(최종 청크·문맥 문자열·두 해시) → `generation-attempt-N`(실제 모델 입력·출력·사용량).
- 기록 메서드는 SDK 오류를 예외로 올리지 않고 `langfuse_status="sdk_error"`와 오류 타입을 남긴다. 기록 실패를 `abstained`로 바꾸지 않으며 서비스 응답은 계속된다.
- `langfuse_status` 값: `disabled`(키 없음) / `sdk_error` / `recorded_unverified`(기록 생성, 저장 미확인). SDK의 `flush()`는 전송 실패를 알려주지 않으므로 저장 확인은 `tracer.verify_trace(trace_id)`가 API에서 관측을 조회해 판단한다.

### 4.2 일반 평가 어댑터와 RunRecord

```python
def adapter(question: str, retrieval_mode: str, request_id: str) -> dict: ...
```

B의 `answer_query(request, resources, request_id, settings)` 결과와 같은 request_id의 내부 기록을 합쳐 [공통 계약 3절](../design/contracts.md)의 RunRecord를 반환한다. `experiment_id`, `question_id`는 실행기가 붙인다. 어댑터에 `tracer` 속성(C의 `Tracer`)이 있으면 실행기가 실행 후 `flush()`하고 각 trace의 저장 여부를 조회한다. 정답·근거·expected_action은 어댑터에 전달하지 않는다.

실행기는 `--adapter 모듈:이름`으로 가져온 속성을 초기화 없이 곧바로 `(question, retrieval_mode, request_id)` 인자로 호출한다. B의 `make_query_adapter(resources, settings)`는 준비된 `Resources`와 `Settings`가 필요한 팩터리이며, 반환하는 callable이 CLI 계약에 맞는다. 팩터리 자체를 `--adapter`에 지정해도 실행기가 팩터리를 호출하거나 초기화하지 않는다. 현재 저장소에는 설정·Resources 초기화, 팩터리 호출, 종료 정리를 묶어 평가 CLI에 제공하는 공식 bootstrap이 없다. 그러므로 아래 명령은 인자 형식을 보여 주는 예시이며, 초기화된 callable을 제공하는 연결부가 준비되기 전에는 그대로 실행 가능한 명령으로 보지 않는다. 가짜 자원 테스트는 있으나 실제 Basic 자료와 서비스 리소스를 사용한 연결은 아직 검증되지 않았다.

상세 RunRecord는 평가 어댑터가 매 호출마다 별도 객체로 수집한다. API에는 기록 sink 옵션이 없다. 문맥 해시는 제품의 공통 함수로 계산하고 별도 C 모듈·해시 콜백 연결을 요구하지 않는다. 전체 제품 패키지를 설치하고 기존 `load_resources()`로 자원을 준비한다.

RunRecord의 필수 필드와 정규화·오류 규칙은 [공통 계약 3절](../design/contracts.md#3-c가-받는-직렬화된-실행-기록)을 기준으로 한다. 유효하지 않은 기록은 점수로 바꾸지 않고 실패 사유와 함께 남긴다.

### 4.3 보안 비교 어댑터

```python
def retrieve_fn(question: str, retrieval_mode: str) -> dict:
    # {"retrieved_candidates": [...평탄 청크], "final_context_chunks": [...평탄 청크]}
    # 어댑터가 이때 만든 ContextBundle을 보관한다.

def generate_fn(question: str, final_context_chunks: list[dict], prompt_variant: str) -> dict:
    # 보관한 ContextBundle로 generate_answer(..., defense="" 또는 app/generation/prompts/defense.txt 내용) 호출.
    # 반환: status, answer, reason, citations, attempts(AttemptRecord), latency_ms,
    #       model_context_text, model_context_hash, request_id, error_type
```

- C는 질문마다 `retrieve_fn`을 한 번만 호출하고 같은 청크(복사본)를 두 변형에 넘긴다.
- 질문별 확인(`security_checks.json`): 로그 청크 해시·순서(`final_context_identical`), 문맥 문자열과 그 해시(`model_context_identical`), 실제 모델 입력에 그 문맥이 그대로 들어갔는지(`context_in_model_input`), `model`·`model_parameters`(`generation_settings_identical`). 값은 True/False/None(확인 불가)이며 False가 있으면 스크립트가 실패로 끝난다.
- 루트 `model_input`이 없으면 마지막 시도의 값을 쓴다. 모델 입력이 없으면 노출은 `exposure_unknown`이다.
- `attack_text`와 공격 문서 ID는 검색·문맥 구성에 쓰지 않고 실행 후 노출 분석에만 쓴다. 결과는 `defended` / `attack_followed` / `not_exposed` / `generation_failed` / `exposure_unknown` / `unjudged`이며 `defended`만 방어 성공으로 센다.
- CLI의 `--retrieve`와 `--generate`도 가져온 callable을 바로 호출한다. B의 `make_security_adapters(resources, settings, *, defense)`는 두 callable을 반환하는 팩터리이므로, 팩터리 이름만 CLI에 지정할 수 없다. 이 연결은 가짜 자원으로 통합 테스트했으며, 실제 Basic 자료·색인·모델을 이용한 실행은 미검증이다. 정확한 반환 기록은 [공통 계약 5절](../design/contracts.md#5-함수-호출-계약)을 따른다.

## 5. 평가 규칙 요약

- **검색 지표:** 정답 집합은 `evidence_groups` 전체 `book_id`의 중복 제거, 검색 집합은 최종 문맥 상위 k개 청크의 `book_id` 중복 제거. 검색 후보 전체는 쓰지 않는다. 참고 문서 없음 → 해당 없음, 최종 문맥이 빈 목록 → 0, 서비스 실패로 최종 문맥 미기록 → 평가 불가. `k`와 실제 전달 청크 수를 함께 저장한다.
- **Faithfulness:** 질문, 생성 출력(answer + reason), 실제 최종 청크 텍스트만 RAGAS에 넘긴다. Judge가 "사실 주장 없음"으로 판단하면 해당 없음. 주장 여부를 모르는 상태에서 RAGAS가 NaN·오류이면 평가 불가. 최종 문맥 없이 사실을 주장하면 0.
- **Judge:** 사실·조건·근거·판단 각각 0/1과 이유. 참고 정답·채점 기준은 이 단계에서만 사용한다. 서비스 실패는 판단 0, 나머지는 평가 불가. 호출·파싱 실패는 전 항목 평가 불가. 형식이 틀린 항목은 평가 불가로 두며 임의 점수로 바꾸지 않는다.
- **평균:** `scored`만 평균하고 `not_applicable`·`unavailable`은 개수로 표시한다. 0점은 평균에 포함한다.
- **운영 지표:** 정상 추가 확인·보류는 오류가 아니다. 오류율 = `error` 요청 수 / 전체 요청 수. 토큰은 재시도 포함 확인값만 더하고 완전/부분/미확인 건수를 표시한다. 일반 benchmark 채점의 시간·확인 가능한 토큰은 `summary.json`의 `grading`에 따로 둔다. RAGAS 토큰은 null이며 이 합계는 금액 비용이 아니다. 보안 Judge의 호출 시간·토큰은 현재 자동 기록되지 않는다.

## 6. 실제 실행

공식 비교는 실제 실행 전에 질문 ID와 선정 이유를 정하고, 선택한 질문 전부를 같은 조건으로 각 검색 방식에 실행한다. 최종 비교 결과를 본 뒤 불리한 문항을 빼지 않는다. PDF·추출·청킹·임베딩·색인·생성 모델·프롬프트·설정·k·문맥 한도는 비교 전에 고정한다. 개인 `experimental/<GitHubID>/` 실험에는 별도 폴더 구조나 기록 형식을 요구하지 않는다. 이 재현 증거 규칙은 팀 공식 비교·제출 결과에 적용한다.

유료·외부 서비스 호출 전에 별도의 **오프라인 입력 점검**을 먼저 한다. 질문·참고 파일의 존재와 JSON 구조, 질문의 `id`·`question`, 선택한 ID와 참고 문항의 대응, 제공 정답 스키마의 근거·채점 필드를 확인한다. 특히 `evidence_groups`가 의도적으로 빈 값인지 필드가 잘못 빠진 것인지 구분한다. 이 점검은 라벨을 서비스에 전달하는 단계가 아니다. 현재 CLI는 참고 파일을 서비스 실행·기록 저장 뒤에 읽고 필수 평가 필드를 선행 검증하지 않으므로, 입력을 확인하지 못한 상태에서 실제 호출을 시작하지 않는다.

입력 점검과 서비스 초기화가 끝나면 일반 benchmark는 `--ids <단일 ID>`로 한 문항만 선택해 연결을 확인한다. 다음 블록은 placeholder를 포함한 **한 문항 사전 확인용 CLI 인자 형식 예시**이며 복사해 실행할 수 있는 명령이 아니다. 현재 adapter 인자는 초기화된 callable을 요구하지만 공식 bootstrap이 없고, `--ragas-model`도 현재 공통 설치에 포함되지 않는다. 이 두 연결이 마련되지 않은 상태를 완료된 실제 평가로 표시하지 않는다.

```bash
python -m app.evaluation.scripts.run_benchmark \
  --questions data/basic/evaluation/development_questions.json \
  --references data/basic/evaluation/development.json \
  --adapter <초기화된 callable을 노출하는 모듈>:<callable> \
  --ids <단일ID> \
  --modes naive \
  --config configs/basic.yaml \
  --judge-model <평가 모델> \
  --ragas-model <평가 모델> \
  --experiment-id basic-<날짜>-<설명> \
  --selection-reason "<선정 이유>"
```

질문 파일에는 `id`, `question`만 둔다. 정답·근거와 채점 라벨은 대응하는 `development.json`에서 실행 기록 저장 뒤에 읽는다. 실제 Basic 자료는 질문 파일과 참고 파일이 분리되어 있으므로 두 경로를 모두 지정한다. CLI의 `--references` 기본값은 `--questions`와 같은 파일이라, 생략하면 제공 데이터의 정답 파일에 연결되지 않는다. 현재 코드의 기본 검증은 참고 문항 ID가 있는지 확인하며 참조 파일의 필수 평가 필드까지 검증하지 않는다.

한 문항 확인 후에는 새 실험 ID를 쓰고 `--ids`를 사전에 고정한 전체 선택 ID 목록, `--modes`를 비교할 방식 목록으로 바꾼다. 사전 확인 예시는 `naive` 한 방식부터 지정한다. 세 검색 방식의 코드는 구현되어 있지만 실제 생성 모델·평가기까지 연결한 공식 비교는 별도 검증이 필요하다. 현재 데이터에는 `development`, `additional`, `expansion_validation` 세 질문 쌍이 있다. 이 예시는 `development`를 사용한다. split의 최종 공식 역할과 validation holdout 운용 여부는 팀이 비교 전에 정해 결과에 기록해야 하며, 이름만으로 독립 검증을 완료했다고 간주하지 않는다. 보안 CLI에는 `--ids`가 없으므로 아래 보안 실행 형식과 별도로 파일·연결을 점검한다.

```bash
python -m app.evaluation.scripts.run_security --security-file data/basic/evaluation/<보안 파일>.json --retrieve <초기화된 retrieve callable 모듈>:<이름> --generate <초기화된 generate callable 모듈>:<이름> --retrieval-mode <방식> --config configs/basic.yaml --judge-model <평가 모델> --experiment-id basic-<날짜>-<설명>
```

```bash
python -m app.evaluation.scripts.run_benchmark --verify-langfuse <trace_id>
```

- 실행 단계는 질문 파일에서 `id`, `question`만 읽는다. 정답 파일(`--references`)은 실행 기록을 `artifacts/`에 저장한 뒤 채점 단계에서 읽는다. 제공 데이터처럼 파일이 나뉘어 있으면 위 명령처럼 쌍을 명시한다.
- 일반 평가는 실행 후 각 trace를 Langfuse API로 조회해 `langfuse_verification`(`verified` / `not_found` / `failed`)을 기록한다. 전체 대기 한도는 `--langfuse-verify-timeout`(기본 90초)이다.
- `--judge-model`·`--ragas-model`을 생략하면 해당 평가를 `unavailable`("평가기 미설정")로 기록한다.
- 같은 실험 ID의 결과가 있으면 덮어쓰지 않고 중단한다. 설정·프롬프트가 바뀌면 새 실험 ID를 쓴다.

### 공식 비교 재현 증거

평가 manifest의 일부 provenance는 자동 기록되지만, 공식 결과를 단독으로 재실행할 만큼의 데이터·환경 지문은 아직 자동으로 모이지 않는다. 다음을 자동 기록, 실행자가 보충할 항목, 후속 구현 항목으로 구분한다.

| 구분 | 현재 상태와 공식 결과에서 할 일 |
| --- | --- |
| 자동 기록 | manifest에 코드 commit/dirty 여부, Python과 일부 패키지 버전, 설정 파일 hash, 실험 ID, 질문 ID·파일명, mode·k, 선정 이유, Judge/RAGAS 모델명, Judge prompt hash를 남긴다. 선정 이유는 현재 선택 입력이라 비어 있을 수 있다. 예시처럼 `--config`를 쓰면 설정 hash는 해당 파일 원문 기준이다. B adapter는 실행 기록에 `latency_scope`도 넣지만 제출 보고서에는 아직 표시하지 않는다. |
| 실행자가 보충 | 공식 결과와 함께 실제 실행 명령, 코드 commit SHA(미커밋 변경이면 해당 diff), 질문 파일과 정답 파일 각각의 SHA-256, `artifacts/basic/index_manifest.json`의 사본 또는 SHA-256, 선택한 색인 컬렉션, 적용된 설정 파일과 유효한 비밀 아닌 설정값·생성 prompt·평가 prompt 버전, 사용한 의존성 잠금/설치 환경을 보존한다. 현재 측정 범위(`in_process_adapter` 등)도 제출 결과에 명시한다. API 키와 인증 환경변수 값은 기록하지 않고, 환경 증거는 의존성 잠금/패키지 버전으로 남긴다. 데이터 파일은 Git에 없고 index manifest는 평가 manifest에 자동 연결되지 않는다. |
| 후속 구현 | 평가 manifest에 질문·참조·색인 manifest hash와 유효 설정/환경 지문을 자동 연결하고, 참조 파일 필드 검증·adapter bootstrap·RAGAS 설치/호환성 절차를 추가해야 재현 가능한 공식 실행 경로가 된다. 현재 완료된 기능으로 표시하지 않는다. |

현재 B adapter는 `answer_query()`를 프로세스 안에서 직접 부르고 `latency_scope="in_process_adapter"`를 기록한다. 이는 HTTP 요청의 전체 처리 시간이 아니라 서비스 내부 측정이다. [공통 계약](../design/contracts.md)은 `http`, `service`/`in_process_adapter`, `fixed_context_generation` 범위를 구분하고 서로 섞지 않도록 한다. 현재 보고서의 비교표에는 `latency_scope`가 자동 표시되지 않으므로 공식 비교 결과에 측정 범위를 수동으로 기록한다. HTTP 미들웨어의 `http` 요약 로그는 표준 로그에 남고 RunRecord에 자동 연결되지 않는다. HTTP 시간을 공식 지표로 채택하려면 같은 요청 ID의 로그·서비스 기록을 연결하고 범위별 보고를 검증해야 한다.

서비스 생성 호출 한도와 Judge·RAGAS 평가 호출 한도는 구분한다. 서비스 생성에는 `configs/basic.yaml`의 시간·출력·재생성 제한이 적용되지만, 평가기 호출에는 프로젝트 차원의 시간·토큰·비용 budget이 아직 없다. 일반 benchmark Judge의 사용량 일부와 grading 시간은 기록되고 RAGAS token은 null이다. 보안 Judge는 현재 호출 시간·토큰·비용을 자동 기록하지 않으므로 제공자 사용량과 실행 기록을 대조해 별도로 집계한다. 누락된 사용량을 0으로 처리하지 않는다. 호출 단가·총액 기준·담당자·중단 기준은 미정이며 [협업 가이드](../../CONTRIBUTING.md)의 운영 절차에서 정해야 한다. 공식 비교 결과에는 재현 증거를 기록한다. 개인 실험의 기록은 자율이다. 실제 비교에 앞서 한 문항의 호출 수·시간·사용량을 확인하고, 이 운영 기준이 정해지기 전에는 전체 비교를 시작하지 않는다.

출력:

| 위치 | 파일 |
| --- | --- |
| `artifacts/experiments/<id>/` (Git 제외) | `manifest.json`, `runs.jsonl`(최종 문맥 텍스트·모델 입력 포함), `scores.jsonl`, `security_runs.jsonl` |
| `reports/<id>/` (제출) | `README.md`, `manifest.json`, `comparison.csv`, `answers.jsonl`, `scores.jsonl`, `summary.json`, `security_comparison.csv`, `security_checks.json` |

## 7. 실제 서비스·평가기 연결 미검증

1. 실제 Basic 질문·정답·보안 파일 구조를 열어 `_items()`·`evidence_groups`·`attack_text` 키가 맞는지 확인한다.
2. B의 실제 요청 한 건으로 Langfuse UI와 `--verify-langfuse`에서 `rag-query` → `retrieval` → `final-context` → `generation-attempt-N`이 보이는지 확인한다.
3. 선택한 Judge 모델의 Chat Completions·JSON 응답 형식과 RAGAS 연결을 확인하고, 실제 한 문항으로 평가해 형식 준수율·호출 시간·토큰을 살핀 뒤 대표 점수를 PDF 원문과 대조한다. 실제 모델·평가기 호환성은 검증하지 않았다.
4. `latency_scope`별 실제 `latency_ms` 범위와 시도별 SDK 토큰 사용량이 실제 요청에서도 기대와 일치하는지 확인한다. 현재 benchmark adapter는 in-process 측정이며 HTTP 전체 요청 시간과 다르다.

[문서 목차로 돌아가기](../README.md)
