"""평가 CLI가 `모듈:이름`으로 불러 쓰는 실제 서비스 연결.

일반 평가:
    python -m app.evaluation.scripts.run_benchmark --config configs/basic.yaml \
        --adapter app.service.evaluation_entry:query_adapter --questions <질문> --references <정답>
보안 평가:
    python -m app.evaluation.scripts.run_security --config configs/basic.yaml \
        --retrieve app.service.evaluation_entry:security_retrieve \
        --generate app.service.evaluation_entry:security_generate \
        --security-file <보안 파일> --retrieval-mode hybrid_rerank

평가 CLI는 --config 경로를 ADEN_CONFIG로 넘기고, 여기서 같은 파일로 설정을 읽는다.
자원은 첫 사용 때 한 번 준비해 일반·보안 어댑터가 함께 쓰고, 프로세스가 끝날 때 닫는다.
"""

import atexit
import os
from pathlib import Path

from dotenv import load_dotenv

from app.service.adapters import make_query_adapter, make_security_adapters
from app.service.resources import load_resources
from shared.settings import CONFIG_ENV, DEFAULT_CONFIG, PROJECT_ROOT, load_settings

DEFENSE_PROMPT = PROJECT_ROOT / "app" / "generation" / "prompts" / "defense.txt"


class EvaluationRun:
    """평가 실행 한 번의 설정·자원·어댑터를 소유한다."""

    def __init__(self):
        self._resources = None
        self._settings = None
        self._query = None
        self._security = None

    def prepare(self):
        if self._resources is None:
            load_dotenv(PROJECT_ROOT / ".env")
            settings = load_settings(Path(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG))
            self._resources = load_resources(settings)
            self._settings = settings
            atexit.register(self.close)
        return self._resources, self._settings

    def close(self):
        resources, self._resources = self._resources, None
        self._settings = self._query = self._security = None
        if resources is not None:
            resources.close()

    @property
    def tracer(self):
        """run_benchmark가 실행 뒤 Langfuse 저장을 확인할 때 쓴다."""
        return self.prepare()[0].tracer

    def __call__(self, question, retrieval_mode, request_id):
        if self._query is None:
            self._query = make_query_adapter(*self.prepare())
        return self._query(question, retrieval_mode, request_id)

    def security_adapters(self):
        if self._security is None:
            resources, settings = self.prepare()
            defense = DEFENSE_PROMPT.read_text(encoding="utf-8")
            self._security = make_security_adapters(resources, settings, defense=defense)
        return self._security


query_adapter = EvaluationRun()


def security_retrieve(question, mode):
    return query_adapter.security_adapters()[0](question, mode)


def security_generate(question, final_context_chunks, prompt_variant):
    return query_adapter.security_adapters()[1](question, final_context_chunks, prompt_variant)
