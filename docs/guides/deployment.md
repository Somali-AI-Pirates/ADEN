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


## 자동 배포 비밀 전달

CI의 재사용 release 호출은 ADEN_DEPLOY_SSH_KEY 하나만 이름으로 전달하고, release 워크플로는 이를 필수 입력으로 선언한다. 실제 값은 release job의 production 환경에서 선택된다. 필수 선언은 전달 여부만 검사하므로, 실행 단계의 빈 값 검사도 유지한다. [GitHub 재사용 워크플로 규칙](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)

ADEN_DEPLOY_ENABLED가 false이면 실제 배포 단계는 실행하지 않는다. 로컬 YAML/계약 검사 통과와 GitHub에서의 실제 비밀 주입·서버 적용 성공을 구분한다. 현재 서버·배포 활성 상태는 [클라우드 기록](../plans/advanced-rag/cloud-setup-record.md)을 따른다.

## 운영 첫 배포 준비

배포 대상은 기존 OCI ARM64 서버의 ADEN 전용 경로다. 첫 적용 전 [클라우드 준비 기록](../plans/advanced-rag/cloud-setup-record.md)의 서버·HTTPS·배포 키와 현재 가용 자원을 대조한다. Docker 이미지의 실제 디스크 사용량, 번들·모델·이전 릴리스 보관 공간, 6GiB·1 CPU에서의 모델 적재와 추론을 확인한다. readiness는 실행 속도나 요청 예산 충족을 보장하지 않으므로 실제 검색 지연과 메모리 여유를 별도로 확인한다. 현재 OCI 측정과 FP32 미채택 근거는 [후속 검증 기록](../plans/advanced-rag/local-refactor-progress.md)에 있다.

| 서버 경로 | 내용 |
| --- | --- |
| `/etc/aden/compose.production.yaml` | 검토한 [운영 Compose](../../infra/compose.production.yaml) |
| `/etc/aden/runtime.yaml` | 검토한 [CPU 설정](../../infra/production/runtime.example.yaml), 실제 Cloud 주소 포함 |
| `/etc/aden/runtime.env` | root 소유 0600 인증 파일 |
| `/srv/aden/shared/bundle` | 검사한 Advanced 번들 |
| `/srv/aden/shared/documents` | 검증한 원본 PDF와 `asset-manifest.json` |
| `/srv/aden/shared/models` | 고정 모델 리비전의 HF 캐시와 `asset-manifest.json` |
| `/srv/aden/shared/runs` | UID 994·GID 985의 API 사용자에 쓰기 권한이 있는 영속 경로 |

번들은 `prepare_table_rag check`로 검사하고 모델 캐시는 `prepare_table_rag models`에서 고정 리비전을 준비한다. 원본과 모델 manifest는 `{"schema_version":1,"files":{"상대/경로":"64자리 SHA-256"}}` 형식이다. 검증한 원본과 모델 파일의 실제 지문을 넣는다. 모델 캐시의 snapshot 링크는 캐시 디렉터리 안의 파일을 가리켜야 한다. 번들·원본·모델은 root가 관리하고 컨테이너에 읽기 전용으로 연결한다. 이 세 디렉터리와 릴리스 상위 디렉터리는 root 소유로 두고 읽기·탐색 권한을 확인한다. API 계정은 번들·원본·모델을 읽고 `runs`에 쓸 수 있어야 한다. 배포 도구는 이미지 pull 전에 여유 공간 4GiB, pull 뒤 2GiB를 검사한다.

[정책 예시](../../infra/production/deploy-policy.example.json)를 `/etc/aden/deploy-policy.json`에 root 소유 0600으로 두고, `approved_sha256`에 다음 다섯 파일의 SHA-256을 기록한다.

- `compose`와 `runtime_config`는 각각 승인한 Compose와 runtime YAML.
- `bundle_manifest`는 번들의 `corpus.json`.
- `documents_manifest`와 `models_manifest`는 각 디렉터리의 `asset-manifest.json`.

설정·데이터·실행 기록의 복구 사본을 ADEN 전용 백업 경로에 만들고 복구 시험을 한다. 실행 기록에는 답변과 근거가 들어 있으므로 같은 접근 정책으로 관리한다. 로그 회전, 디스크 여유 경보, 이전 이미지·릴리스 보존을 확인한 뒤 적용한다. Compose·데이터 변경 시 승인 지문을 갱신한다.

운영 관리자가 검증한 커밋에서 [고정 명령 설치](../../infra/production/install-aden-release.sh)와 [Nginx 설정](../../infra/production/README.md)을 적용한다. 설치기는 기존 authorized key가 사용하는 `/usr/local/bin/aden-deploy-gate` 경로에 고정 명령을 설치한다. 배포 상태 디렉터리는 root 전용이며 공개 웹 릴리스 경로는 Nginx가 읽을 수 있어야 한다. 첫 배포 전에는 서버 정책의 `deployment_enabled`와 GitHub `production` 변수 `ADEN_DEPLOY_ENABLED`를 false로 둔다. 승인된 데이터·인증·용량·복구 검증과 함께 활성화한다. 최초 게시 후 두 GHCR 패키지의 이미지 내용과 공개 범위를 검토하고, 운영 서버에서 인증 없이 digest로 읽을 수 있는지 확인한다. 새 패키지의 초기 공개 범위는 private이다. [GHCR 패키지 게시와 접근](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#pushing-container-images) 배포용 SSH 키의 호스트 검증과 `production`의 main 브랜치 제한을 확인한다.

## 배포와 복구

main의 모든 검사가 통과하면 같은 SHA에서 검사한 ARM64 API·웹 이미지를 GHCR에 게시한다. 배포 job은 활성화된 `production`에서 이미지 digest·SHA·실행 번호를 고정 SSH 명령 `aden-deploy`에 보낸다. 서버에서 자산 지문과 이미지 출처를 대조하고, loopback readiness가 준비되면 웹 경로를 전환해 HTTPS·API·PDF Range와 PDF.js worker의 HTTP 응답을 확인한다. Worker의 `Content-Type`은 `text/javascript`여야 하며, [ADEN Nginx 설정](../../infra/production/aden.conf)이 해당 경로에 이 타입을 지정한다.

상태는 배포 키의 `aden-status`로 확인한다. 배포 클라이언트는 `infra/deploy.sh RELEASE_JSON`, 복구 클라이언트는 `infra/rollback.sh`다. 둘 다 `ADEN_DEPLOY_HOST`, `ADEN_DEPLOY_USER`, `ADEN_DEPLOY_KEY_FILE`, `ADEN_SSH_KNOWN_HOSTS`를 사용하며 키와 known_hosts는 파일 경로로 전달한다. 관리자가 복구를 지시하면 `aden-rollback`으로 이전 이미지·Compose·YAML을 적용한다. 실패 자동 복구와 수동 복구는 이전 릴리스에 필요한 데이터 지문을 검증한다. 릴리스는 `/srv/aden/releases/`, 활성 웹은 `/srv/aden/current`, 배포 상태와 이력은 `/var/lib/aden/deploy/`에 저장된다. 복구가 실패하면 상태와 로그를 보존한 뒤 자산·권한·readiness를 확인한다. 새 배포와 실행 기록 정리를 별도로 관리한다.
