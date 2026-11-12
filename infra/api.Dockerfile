# CPU runtime image for the existing Advanced TableRAG policy.
ARG PYTHON_IMAGE=python:3.13.18-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1

FROM ${UV_IMAGE} AS uv-bin
FROM ${PYTHON_IMAGE} AS runtime
COPY --from=uv-bin /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/aden \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/aden/.venv \
    PATH=/opt/aden/.venv/bin:${PATH} \
    HF_HOME=/opt/models \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TIKTOKEN_CACHE_DIR=/opt/aden/tokenizer-cache \
    TOKENIZERS_PARALLELISM=false

WORKDIR /opt/aden
COPY pyproject.toml uv.lock ./
COPY app ./app
COPY shared ./shared
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra cpu --no-editable
RUN mkdir -p /opt/aden/tokenizer-cache \
    && /opt/aden/.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"

RUN groupadd --gid 985 aden \
    && useradd --uid 994 --gid aden --home-dir /home/aden --create-home --shell /usr/sbin/nologin aden \
    && mkdir -p /etc/aden /opt/aden/artifacts/rag-runs /opt/models \
    && chown -R 994:985 /opt/aden/artifacts/rag-runs /opt/aden/tokenizer-cache /home/aden

USER 994:985
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8000/health/live\", timeout=2)"]
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
