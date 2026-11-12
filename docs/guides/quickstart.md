# Advanced TableRAG 빠른 시작

VS Code의 WSL Ubuntu 터미널에서 저장소 루트를 기준으로 실행한다. 기본 서버는 공통 SQLite에서 HG·HA·IS 기록을 조회하고, 필요한 문서와 규정은 페이지·표 하이브리드 검색으로 찾는다. 계획·답변 모델은 GPT-6 Luna medium이다. TableRAG의 dense 벡터 검색은 기본 NumPy이며, 이 빠른 시작은 로컬 YAML의 `table_rag.vector_backend: qdrant`로 Qdrant Cloud exact 검색을 선택한다. 질문 임베딩과 KURE nano 리랭커, BM25, SQLite, 원문·출처는 로컬에서 유지한다.

## 1. 설치와 로컬 설정

Python 3.13과 uv를 준비한다. 원본 PDF 처리에는 Poppler의 pdftotext가 필요하다.

```bash
sudo apt-get update
sudo apt-get install poppler-utils
uv sync --locked --extra test --extra eval
```

[인증값 예시](../../.env.example)를 참고해 루트 `.env`에 OpenAI, Qdrant Cloud, Langfuse 인증값을 넣는다. [개인 설정 예시](../../configs/local.example.yaml)를 `configs/local.yaml`로 복사해 장치·주소·컬렉션을 설정한다.

```yaml
table_rag:
  device: cuda
  vector_backend: qdrant
  collection: advanced_table_rag
  trace_enabled: true

connections:
  qdrant_url: https://example.cloud.qdrant.io:6333
  langfuse_base_url: https://cloud.langfuse.com
```

CUDA를 사용할 수 있는 PyTorch 환경에서는 `device: cuda`로 질문 임베딩과 KURE nano 추론을 GPU에서 실행한다. CPU 환경은 `device: cpu`를 선택한다. Qdrant Cloud 예시 주소를 클러스터 주소로 바꾼다. Langfuse 추적은 질문·답변·최종 근거와 모델·도구 입출력을 지정한 프로젝트에 전송한다. 실행 기록만 남기려면 `trace_enabled: false`를 설정한다.

## 2. SQLite와 검색 자료 준비

원본 Advanced PDF 120개를 data/advanced/documents/AS001.pdf부터 AS120.pdf까지 배치한다. 제공 PDF와 DB·벡터는 Git에 포함되지 않으므로 저장소를 받은 뒤 별도로 준비해야 한다.

```bash
uv run --locked python -m app.ingestion.scripts.prepare_table_rag build --documents data/advanced/documents --output artifacts/advanced-table-rag
uv run --locked python -m app.ingestion.scripts.prepare_table_rag check --directory artifacts/advanced-table-rag
```

모델만 먼저 준비하려면 `uv run --locked python -m app.ingestion.scripts.prepare_table_rag models`를 실행한다. 고정한 KURE-v3와 KURE nano 리비전을 다운로드한다. 구축은 YAML 장치를 사용하며 `--device`로 한 실행의 장치를 지정할 수 있다.

첫 구축은 모델 준비와 전체 임베딩 때문에 시간이 걸린다. GPU에서 구축한 번들은 같은 모델을 사용하는 CPU 서버에서 그대로 읽을 수 있다. 완료되면 records.sqlite3, tables.sqlite3, 페이지·표 자료, 벡터와 지문 파일이 한 디렉터리에 생긴다. 현재 번들의 `vectors.npy`에는 KURE-v3 2,048차원 벡터 21,750개(페이지 18,000개와 표 검색 단위 3,750개)가 있다. 완성된 번들이 있으면 check부터 실행한다. [데이터 구조와 구축](data.md#advanced-tablerag-데이터)

중단한 구축은 같은 명령으로 재개한다. 입력 PDF·파서·모델 조건이 일치하는 완료 단계만 재사용한다. 강제 종료 후 잠금 파일 오류가 나면 오류에 표시된 프로세스가 종료됐는지 확인한 뒤 해당 잠금 파일만 제거한다. 작업 디렉터리와 체크포인트는 유지한다. 완성된 번들은 자동으로 덮어쓰지 않는다. 입력이나 구축 코드의 지문이 달라진 중간 작업은 재사용하지 않는다. 이전 코드로 재개하거나 새 `--work-dir`와 출력 경로에 구축·검사한 뒤 `table_rag.data_directory`로 해당 번들을 선택한다.

Qdrant Cloud를 선택했다면 로컬 벡터 번들 준비가 끝난 뒤, 서버를 실행하기 전에 기존 `vectors.npy`를 업로드하고 검사한다. 이 작업은 벡터를 다시 임베딩하지 않는다.

```bash
uv run --locked python -m app.ingestion.scripts.prepare_table_rag upload-qdrant --directory artifacts/advanced-table-rag
uv run --locked python -m app.ingestion.scripts.prepare_table_rag check-qdrant --directory artifacts/advanced-table-rag --full
```

기본 컬렉션 이름은 `advanced_table_rag`다. 다른 이름을 쓸 때는 `advanced_table_rag` 또는 `advanced_table_rag_`로 시작하는 이름만 사용하고, YAML의 `table_rag.collection`을 설정한다. 업로드·검사 명령도 이 값을 읽으며, `--collection`을 명시하면 해당 실행에서만 우선한다. 업로드와 검사가 끝난 뒤 서버를 시작한다. [Qdrant Cloud 저장 범위와 검증](data.md#advanced-tablerag-qdrant-cloud-벡터-검색) · [로컬 Qdrant와 선택 Langfuse](deployment.md#로컬-qdrant)

## 3. 서버와 Swagger

```bash
uv run --locked fastapi dev
```

[현재 준비 상태](http://127.0.0.1:8000/health/ready)가 HTTP 200과 `ready=true`를 반환하는지 확인한다. 기존 `/health`의 `query_ready`는 시작 시 초기화 결과를 보여주며, 실행 중 저장소 상태는 `/health/ready`로 확인한다. [Swagger UI](http://127.0.0.1:8000/docs)에서 POST /query를 열고 Try it out을 누른다.

```json
{"question": "예약 접수번호를 받으면 예약이 확정된 건가요?"}
```

응답의 request_id는 X-Request-ID 헤더, 앱 로그, 로컬 기록과 Langfuse 메타데이터를 연결한다. 로컬 기록은 artifacts/rag-runs/<request_id>/manifest.json과 runs.jsonl에 저장된다.

DB·모델 준비가 끝나지 않았거나 Qdrant Cloud 컬렉션을 사용할 수 없거나 호환되지 않으면 TableRAG 준비가 실패하고 `query_ready`는 false이며 `/query`는 503을 반환한다. 데이터 검사 결과와 서버의 준비 오류를 먼저 확인한다. Qdrant 오류가 나도 NumPy로 자동 전환하지 않는다. 이전 로컬 검색 경로를 사용하려면 YAML에 `table_rag.vector_backend: numpy`를 지정하고 서버를 다시 시작한다. 서버는 시작할 때 선택된 벡터 backend와 로컬 모델을 준비한다.

번들 위치는 `table_rag.data_directory`에 설정한다. RAG CLI와 TableRAG 평가 run에서 `--data-directory`를 명시하면 해당 실행에 우선한다.

```bash
uv run --locked python -m app.rag.cli "예약 접수번호를 받으면 예약이 확정된 건가요?"
```

설정은 **명시 CLI 인자 → 프로세스 환경 → 기존 루트 `.env` 설정 → YAML → 기본값** 순으로 적용된다. 기본 실행은 `configs/advanced.yaml`에 `configs/local.yaml`의 `documents_dir`, `table_rag`, `connections`를 적용한다. `--config` 또는 프로세스의 `ADEN_CONFIG`로 파일을 선택하면 해당 YAML을 사용한다. 상대 경로는 저장소 루트 기준이다. 기록 경로는 `table_rag.run_directory`로 바꾼다. 고정 검색과 모델 설정은 [공통 정책](../../shared/table_rag_policy.py)을 따른다.

### 상태 확인과 요청 제한

| 경로 | 확인 범위 |
| --- | --- |
| `GET /health` | 기존 초기화 상태·데이터셋. 응답 형식과 의미 유지 |
| `GET /health/live` | HTTP 처리 가능 여부. 외부 저장소·모델 호출 없음 |
| `GET /health/ready` | 초기화·SQLite 읽기·선택 벡터 저장소·실행 기록 쓰기·종료 여부. 준비되면 200, 아니면 503 |

준비 검사는 10초 동안 성공·실패 결과를 캐시하고 동시 검사는 하나로 합친다. Qdrant는 2초 연결·요청 제한으로 기존 컬렉션의 상태와 호환 정보를 읽는다. 모델 추론·전체 벡터 검사는 하지 않는다. 실행 기록 경로에 임시 파일을 쓰고 동기화·삭제하며, 원본·SQLite·벡터는 바꾸지 않는다. Langfuse 장애나 대기열 혼잡은 readiness 실패 사유가 아니다. 응답에는 구성요소별 참·거짓만 표시한다.

TableRAG 서비스 하나의 기본 수용 한도는 **실행 1개·FIFO 대기 2개**다. 대기는 최대 10초, 준비 확인·대기를 포함한 요청 예산은 300초다. 서버·RAG CLI·TableRAG 평가 `run`이 같은 설정을 사용한다. 독립 프로세스 사이의 전역 한도나 별도 Judge·Ragas 채점의 한도는 아니다.

| 상황 | HTTP 응답 |
| --- | --- |
| 대기열 초과 또는 대기 시간 초과 | 429, `Retry-After: 10` |
| 자원 미준비 또는 서비스 종료 중 | 503 |
| 요청 처리 예산 초과 | 504 |

SSE는 헤더 전까지 같은 준비·수용 검사를 한다. 스트림이 시작된 뒤 실패하면 `error`로 끝나며 `final`을 보내지 않는다. 성공 `final`은 답변 검증과 저장이 끝난 뒤 보낸다.

300초는 취소를 시작하는 시점이다. 이미 실행 중인 동기 추론·SQL·저장 작업은 강제 중단하지 않고 완료까지 기다리며 슬롯과 자원을 유지한다. 따라서 오류 반환이나 서비스 종료가 예산보다 늦을 수 있다. 서비스 종료 시 새 요청과 대기를 중단하고 실행 요청에 최대 30초를 준 뒤 취소·동기 작업 정리·공유 자원 종료를 진행한다. [Python 작업 스레드의 취소 제약](https://anyio.readthedocs.io/en/stable/threads.html#reacting-to-cancellation-in-worker-threads)

한도를 변경하려면 [Advanced 설정](../../configs/advanced.yaml)의 `table_rag`에서 `max_active_requests`, `max_waiting_requests`, `queue_timeout_seconds`, `request_timeout_seconds`, `shutdown_grace_seconds`를 지정한다.

## 4. 저장된 Swagger 답변 평가

아래 REQUEST_ID를 응답의 request_id로 바꾼다. Faithfulness는 저장된 답변과 실제 최종 근거를 사용한다.

```bash
REQUEST_ID="응답에서_확인한_request_id"
uv run --locked --extra eval python -m app.evaluation.scripts.run_table_rag grade --run-directory "artifacts/rag-runs/$REQUEST_ID" --grade-id faithfulness --ragas-model gpt-6-luna
uv run --locked python -m app.evaluation.scripts.run_table_rag publish --grade-directory "artifacts/rag-runs/$REQUEST_ID/grades/faithfulness" --verify
```

참고답이 있으면 --references와 --judge-model gpt-6-luna를 함께 지정해 검색 지표·SQL 기대값·4축 Judge까지 채점한다. Swagger 실행의 question_id는 request_id다. 직접 만든 참고 파일은 같은 ID를 사용한다. 기존 평가 문항의 ID에 연결하려면 --reference-id를 지정한다. 재채점은 새 grade-id로 저장한다. [평가 파일 형식과 공식 문항 실행](c-module-guide.md#tablerag-평가)

기존 공식 질문 AS-DEV01과 동일한 질문을 Swagger에서 실행했다면 다음처럼 연결한다.

```bash
uv run --locked --extra eval python -m app.evaluation.scripts.run_table_rag grade --run-directory "artifacts/rag-runs/$REQUEST_ID" --references data/advanced/evaluation/development.json --reference-id AS-DEV01 --grade-id reference --judge-model gpt-6-luna --ragas-model gpt-6-luna
```

점수는 grades/<grade-id>/scores.jsonl에 저장된다. Langfuse에서는 같은 요청의 rag-query trace와 게시된 scores를 확인한다. 평가기 오류는 unavailable 사유로 표시되며, --verify는 실제 원격 점수 저장 여부를 확인한다. 한 trace의 점수를 묶어서 조회하고 점수 ID·값·게시 날짜를 대조한다. 수집 지연이 있으면 trace별로 최대 5초의 재조회 기간을 사용하며, 전체 조회 시간은 trace 수와 네트워크 응답 시간에 따라 늘어난다.

## 5. 개발 검사

[검사 명령](development.md#검사)과 [현재 구현 기록](../plans/advanced-rag/unified-implementation-log.md)을 따른다. 실제 질문 실행과 Ragas·Judge 채점은 OpenAI API를 호출한다.

## 브라우저 채팅과 원문 열람

서버와 원본 PDF가 준비되면 저장소 루트에서 별도 터미널로 실행한다.

```bash
cd web
npm ci
npm run dev
```

[채팅 화면](http://localhost:3000)에서 스트리밍 답변과 출처를 확인한다. 출처를 누르면 해당 PDF 페이지가 열린다. Node.js 22 이상이 필요하며, 브라우저에 API 키를 넣지 않는다. [클라이언트 실행·배포·검사](../../web/README.md)에 스트림 오류, 원본 문서 위치, 정적 배포와 현재 대화 지원 범위를 설명한다.
