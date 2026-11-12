# Advanced TableRAG 빠른 시작

VS Code의 WSL Ubuntu 터미널에서 저장소 루트를 기준으로 실행한다. 기본 서버는 공통 SQLite에서 HG·HA·IS 기록을 조회하고, 필요한 문서와 규정은 페이지·표 하이브리드 검색으로 찾는다. 계획·답변 모델은 GPT-6 Luna medium이다.

## 1. 설치와 로컬 설정

Python 3.13과 uv를 준비한다. 원본 PDF 처리에는 Poppler의 pdftotext가 필요하다.

```bash
sudo apt-get update
sudo apt-get install poppler-utils
uv sync --locked --extra test --extra eval
```

루트 .env에 OPENAI_API_KEY를 설정한다. Langfuse 상세 기록에는 LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL을 함께 설정한다. 기존 .env가 있으면 필요한 값만 확인한다. 변수 이름과 선택 설정은 [.env.example](../../.env.example)에 있다.

Langfuse 키가 있으면 질문·답변·최종 근거와 모델·도구 입출력을 연결된 프로젝트에 전송한다. ADEN_TABLE_RAG_TRACE=0으로 로컬 기록만 사용할 수 있다.

## 2. SQLite와 검색 자료 준비

원본 Advanced PDF 120개를 data/advanced/documents/AS001.pdf부터 AS120.pdf까지 배치한다. 제공 PDF와 DB·벡터는 Git에 포함되지 않으므로 저장소를 받은 뒤 별도로 준비해야 한다.

```bash
uv run --locked python -m app.ingestion.scripts.prepare_table_rag build --documents data/advanced/documents --output artifacts/advanced-table-rag --device cpu
uv run --locked python -m app.ingestion.scripts.prepare_table_rag check --directory artifacts/advanced-table-rag
```

모델만 먼저 준비하려면 `uv run --locked python -m app.ingestion.scripts.prepare_table_rag models`를 실행한다. 고정한 KURE-v3와 KURE nano 리비전을 다운로드한다. CUDA를 사용할 수 있는 환경에서는 구축 명령의 `--device cpu`를 `--device cuda`로 바꿀 수 있다.

첫 구축은 모델 준비와 전체 임베딩 때문에 시간이 걸린다. GPU에서 구축한 번들은 같은 모델을 사용하는 CPU 서버에서 그대로 읽을 수 있다. 완료되면 records.sqlite3, tables.sqlite3, 페이지·표 자료, 벡터와 지문 파일이 한 디렉터리에 생긴다. 완성된 번들이 있으면 check부터 실행한다. [데이터 구조와 구축](data.md#advanced-tablerag-데이터)

중단한 구축은 같은 명령으로 재개한다. 입력 PDF·파서·모델 조건이 일치하는 완료 단계만 재사용한다. 강제 종료 후 잠금 파일 오류가 나면 오류에 표시된 프로세스가 종료됐는지 확인한 뒤 해당 잠금 파일만 제거한다. 작업 디렉터리와 체크포인트는 유지한다. 완성된 번들은 자동으로 덮어쓰지 않는다. 입력이나 구축 코드를 바꿨다면 서버를 끄고 새 출력 경로에 구축·검사한 뒤 `ADEN_TABLE_RAG_DIR`로 해당 번들을 선택한다.

## 3. 서버와 Swagger

```bash
uv run --locked fastapi dev
```

[준비 상태](http://127.0.0.1:8000/health)의 query_ready가 true인지 확인한다. [Swagger UI](http://127.0.0.1:8000/docs)에서 POST /query를 열고 Try it out을 누른다.

```json
{"question": "예약 접수번호를 받으면 예약이 확정된 건가요?"}
```

응답의 request_id는 X-Request-ID 헤더, 앱 로그, 로컬 기록과 Langfuse 메타데이터를 연결한다. 로컬 기록은 artifacts/rag-runs/<request_id>/manifest.json과 runs.jsonl에 저장된다.

DB나 모델 준비가 끝나지 않으면 query_ready는 false이고 /query는 503을 반환한다. 데이터 검사 결과와 서버의 준비 오류를 먼저 확인한다. 서버는 시작할 때 준비된 모델·색인을 로드한다.

CPU가 기본이다. CUDA 리랭킹은 ADEN_TABLE_RAG_DEVICE=cpu_gpu_rerank로 선택한다. 서버의 번들 위치는 ADEN_TABLE_RAG_DIR로 변경할 수 있다. 터미널 CLI도 같은 서비스를 쓰지만 번들 위치는 --data-directory 인자로 받는다.

```bash
uv run --locked python -m app.rag.cli "예약 접수번호를 받으면 예약이 확정된 건가요?"
```

서버에 사용자 지정 번들을 설정했다면 CLI에도 `--data-directory "$ADEN_TABLE_RAG_DIR"`를 붙인다.

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
