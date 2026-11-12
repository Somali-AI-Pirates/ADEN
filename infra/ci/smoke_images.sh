#!/usr/bin/env bash
set -euo pipefail

: "${API_IMAGE:?Set API_IMAGE to the built ARM64 API image tag}"
: "${WEB_IMAGE:?Set WEB_IMAGE to the built ARM64 web image tag}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
api_port="18080"
web_port="13100"
tmp="$(mktemp -d)"
suffix="${GITHUB_RUN_ID:-${tmp##*/}}"
api_name="aden-api-smoke-$suffix"
web_name="aden-web-smoke-$suffix"
web_server=""
cleanup() {
  if [[ -n "$web_server" ]]; then
    kill "$web_server" >/dev/null 2>&1 || true
  fi
  docker rm --force "$api_name" "$web_name" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

cd "$root"
docker run --rm --interactive --read-only --network none --entrypoint python "$API_IMAGE" - <<'PY'
import os
from pathlib import Path

import tiktoken
import torch
from shared.settings import PROJECT_ROOT

assert PROJECT_ROOT == Path("/opt/aden")
assert (os.getuid(), os.getgid()) == (994, 985)
assert torch.version.cuda is None
assert tiktoken.get_encoding("o200k_base").encode("image smoke")
for name in (".env", "configs/local.yaml", "data", "experimental", "tests", "artifacts/advanced-table-rag"):
    assert not (PROJECT_ROOT / name).exists(), name
PY

docker run --detach --rm \
  --name "$api_name" \
  --publish "127.0.0.1:${api_port}:8000" \
  --env ADEN_CONFIG=/etc/aden/runtime.yaml \
  --env PYTHONPATH=/tmp/aden-ci:/opt/aden \
  --volume "$root/configs/advanced.yaml:/etc/aden/runtime.yaml:ro" \
  --volume "$root/infra/ci/api_smoke_sitecustomize.py:/tmp/aden-ci/sitecustomize.py:ro" \
  "$API_IMAGE" >/dev/null

docker create --name "$web_name" "$WEB_IMAGE" /bin/true >/dev/null
mkdir -p "$tmp/web"
docker cp "$web_name:/web/." "$tmp/web/"
test -s "$tmp/web/index.html"
python3 -m http.server "$web_port" --bind 127.0.0.1 --directory "$tmp/web" \
  >"$tmp/web-server.log" 2>&1 &
web_server="$!"

wait_for_http() {
  local url="$1"
  for _ in $(seq 1 60); do
    if curl --silent --fail "$url" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for $url" >&2
  docker logs "$api_name" >&2 || true
  cat "$tmp/web-server.log" >&2 || true
  return 1
}

wait_for_http "http://127.0.0.1:${api_port}/health/live"
wait_for_http "http://127.0.0.1:${web_port}/"
curl --silent --show-error --fail "http://127.0.0.1:${api_port}/docs" >/dev/null
curl --silent --show-error --fail "http://127.0.0.1:${api_port}/health/ready" \
  | python3 -c 'import json, sys; data=json.load(sys.stdin); assert data["ready"] is True'
curl --silent --show-error --fail \
  --header 'content-type: application/json' \
  --data '{"question":"CI smoke question"}' \
  "http://127.0.0.1:${api_port}/query" \
  | python3 -c 'import json, sys; data=json.load(sys.stdin); assert data["status"] == "answered" and data["answer"] == "CI smoke: CI smoke question"'
curl --silent --show-error --fail --output "$tmp/index.html" \
  "http://127.0.0.1:${web_port}/"
grep -Eqi '<!doctype html|<html' "$tmp/index.html"

echo "API image and static web artifact smoke passed."
