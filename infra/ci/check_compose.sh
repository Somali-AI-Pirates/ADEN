#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
: > "$tmp/runtime.env"
printf '%s\n' \
  'ADEN_LF_NEXTAUTH_SECRET=ci-placeholder' \
  'ADEN_LF_POSTGRES_PASSWORD=ci-placeholder' \
  'ADEN_LF_SALT=ci-placeholder' \
  'ADEN_LF_ENCRYPTION_KEY=0000000000000000000000000000000000000000000000000000000000000000' \
  'ADEN_LF_CLICKHOUSE_PASSWORD=ci-placeholder' \
  'ADEN_LF_MINIO_SECRET_KEY=ci-placeholder' \
  'ADEN_LF_REDIS_PASSWORD=ci-placeholder' \
  > "$tmp/observability.env"
printf 'dataset: advanced\ntable_rag:\n  device: cpu\n  vector_backend: numpy\n' > "$tmp/runtime.yaml"

cd "$root"
docker compose --file infra/compose.local.yaml config --quiet
docker compose --env-file "$tmp/observability.env" \
  --file infra/compose.observability.yaml config --quiet
ADEN_API_IMAGE=aden-ci:placeholder \
ADEN_RELEASE_CONFIG="$tmp/runtime.yaml" \
ADEN_RUNTIME_ENV="$tmp/runtime.env" \
  docker compose --file infra/compose.production.yaml config --quiet
