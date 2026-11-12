# TableRAG 컨테이너와 배포

로컬 앱은 [빠른 시작](quickstart.md)의 uv 실행을 사용한다. 여기서는 로컬 Qdrant와 선택 Langfuse, 운영용 이미지와 검증 절차를 다룬다.

## 로컬 Qdrant

Docker Desktop과 WSL 통합 또는 Linux Docker Compose를 준비한다. 기존 Advanced 번들이 있으면 아래 명령으로 벡터를 로컬 Qdrant에 넣는다. 컬렉션은 `advanced_table_rag_local`이고, 저장소는 `aden-qdrant-data` 볼륨에 남는다.

```bash
docker compose --project-name aden-local -f infra/compose.local.yaml up -d
uv run --locked python -m app.ingestion.scripts.prepare_table_rag --config configs/qdrant-local.yaml upload-qdrant
uv run --locked python -m app.ingestion.scripts.prepare_table_rag --config configs/qdrant-local.yaml check-qdrant --full
ADEN_CONFIG=configs/qdrant-local.yaml uv run --locked fastapi dev
```

로컬 주소는 `http://127.0.0.1:6333`이다. `connections.qdrant_local: true`에서 loopback HTTP를 허용한다. 프로필을 Cloud로 전환하려면 Cloud 설정 파일을 선택한다. 로컬 프로필은 환경변수로 외부 URL을 넣어도 그 연결을 거부한다. 명시한 로컬 프로필은 CPU로 실행되므로 CUDA를 함께 쓰려면 이 파일의 사본에 `table_rag.device: cuda`를 지정한다. 로컬 API 호출에도 기존 OpenAI 인증값이 필요하다. 테스트는 readiness와 검색까지 실행할 수 있다.

일시 중지에는 `docker compose --project-name aden-local -f infra/compose.local.yaml stop`을 사용한다. 다시 시작한 뒤 `check-qdrant`로 컬렉션 상태를 확인한다.

## 선택 Langfuse

다음 이름으로 로컬 전용 인증값을 담은 `.env.langfuse` 파일을 준비한다. 값은 각각 별도로 생성한다. `ADEN_LF_ENCRYPTION_KEY`는 64자리 hex 문자열을 사용하고, 아래의 나머지 값에도 충분한 길이의 hex 문자열을 사용할 수 있다.

- `ADEN_LF_NEXTAUTH_SECRET`, `ADEN_LF_SALT`, `ADEN_LF_ENCRYPTION_KEY`
- `ADEN_LF_POSTGRES_PASSWORD`, `ADEN_LF_CLICKHOUSE_PASSWORD`, `ADEN_LF_MINIO_SECRET_KEY`, `ADEN_LF_REDIS_PASSWORD`

```bash
docker compose --project-name aden-observability --env-file .env.langfuse -f infra/compose.observability.yaml up -d --wait --wait-timeout 300
curl --fail http://127.0.0.1:3300/api/public/health
```

브라우저에서 `http://127.0.0.1:3300`을 열어 로컬 프로젝트의 API 키를 발급한다. 로컬 프로젝트를 쓸 때 `connections.langfuse_base_url`에 이 주소를 넣고 `.env`에 해당 프로젝트의 인증값을 지정한다. Cloud 키와 로컬 키는 해당 프로젝트에 맞게 선택한다. 로컬 추적이 필요 없는 실행은 `table_rag.trace_enabled: false`를 사용한다.

## 이미지 검사

```bash
docker build --platform linux/arm64 -f infra/api.Dockerfile -t aden-api:local .
docker build --platform linux/arm64 -f infra/web.Dockerfile -t aden-web:local .
```

API는 Python 3.13과 CPU PyTorch를 잠금 파일로 설치한다. 번들·PDF·모델 캐시는 실행 시 읽기 전용으로 마운트하고 요청 기록은 쓰기 가능한 영속 경로에 둔다. 운영 실행의 장치·경로·연결은 [CPU 런타임 YAML 예시](../../infra/production/runtime.example.yaml)를 기준으로 준비한다. 웹 이미지는 호스트 Nginx가 제공할 `/web` 정적 파일을 담는다. ARM64 실행은 CI의 ARM64 러너에서도 확인한다.
