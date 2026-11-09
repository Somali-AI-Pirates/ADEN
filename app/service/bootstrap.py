"""평가 CLI에 넘길 초기화된 서비스 callable.

평가 CLI는 ``--adapter``·``--retrieve``·``--generate``로 받은 ``모듈:이름``을 import해
곧바로 호출한다. 이 모듈은 그 callable을 제공하고, 설정·Resources 준비와 팩터리 호출,
종료 정리를 맡는다. import만으로는 모델·네트워크를 초기화하지 않고 첫 사용 때 준비한다.

    python -m app.evaluation.scripts.run_benchmark --adapter app.service.bootstrap:query_adapter ...
    python -m app.evaluation.scripts.run_security \
        --retrieve app.service.bootstrap:retrieve_context \
        --generate app.service.bootstrap:generate_with_context ...

설정은 기본적으로 ``configs/basic.yaml``을 읽는다. 평가 CLI의 ``--config``가
있으면 ``configure_evaluation()``을 통해 지정 파일을 실제 자원 초기화에 적용한다. 접속 정보는 앱과
같이 저장소 루트의 ``.env``를 읽으며 이미 설정된 환경변수를 우선한다.
"""

import atexit
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

from app.generation.chain import DEFAULT_PROMPT
from app.service.adapters import make_query_adapter, make_security_adapters
from app.service.resources import Resources, load_resources
from shared.settings import DEFAULT_CONFIG, PROJECT_ROOT, Settings, load_settings

DEFENSE_PROMPT = DEFAULT_PROMPT.parent / "defense.txt"


def load_settings_with_env(config_path: Path = DEFAULT_CONFIG) -> Settings:
    """``create_app()``과 같은 순서로 ``.env``를 읽은 뒤 지정 설정을 검증한다."""
    load_dotenv(PROJECT_ROOT / ".env")
    return load_settings(config_path)


class ServiceBootstrap:
    """평가 실행 한 번 동안 설정·Resources와 어댑터를 한 벌만 준비해 공유한다."""

    def __init__(self, settings_loader=load_settings_with_env, resources_loader=load_resources):
        self._config_path: Path | None = None
        self._settings_loader = settings_loader
        self._resources_loader = resources_loader
        self._settings: Settings | None = None
        self._resources: Resources | None = None
        self._query_adapter = None
        self._security_adapters = None

    def configure(self, config_path: Path) -> None:
        """자원 준비 전에 평가 설정을 선택한다. 실행 중 설정 교체는 거부한다."""
        config_path = Path(config_path).resolve()
        active_path = self._config_path or DEFAULT_CONFIG.resolve()
        if self._resources is not None:
            if config_path == active_path and load_settings(config_path) == self._settings:
                return
            raise ValueError("이미 준비된 평가 자원의 설정을 바꿀 수 없습니다. 먼저 close()하거나 새 실행을 사용하세요.")
        self._config_path = config_path
        self._settings_loader = partial(load_settings_with_env, config_path)
        self._settings = None

    def resources(self) -> Resources:
        if self._resources is None:
            self._settings = self._settings_loader()
            self._resources = self._resources_loader(self._settings)
            atexit.register(self.close)
        return self._resources

    def query_adapter(self):
        if self._query_adapter is None:
            self._query_adapter = make_query_adapter(self.resources(), self._settings)
        return self._query_adapter

    def security_adapters(self):
        if self._security_adapters is None:
            resources = self.resources()
            defense = DEFENSE_PROMPT.read_text(encoding="utf-8")
            self._security_adapters = make_security_adapters(resources, self._settings, defense=defense)
        return self._security_adapters

    def close(self):
        """Resources를 해제한다. 추적기 종료(전송 마무리)도 Resources 정리에 포함된다."""
        resources, self._resources = self._resources, None
        self._query_adapter = self._security_adapters = None
        if resources is not None:
            atexit.unregister(self.close)
            resources.close()


class _QueryAdapter:
    """일반 평가 CLI용 adapter. 실행기가 읽는 ``tracer`` 속성을 읽을 때 자원을 준비한다."""

    def __init__(self, bootstrap: ServiceBootstrap):
        self._bootstrap = bootstrap

    @property
    def tracer(self):
        return self._bootstrap.resources().tracer

    def __call__(self, question, retrieval_mode, request_id):
        return self._bootstrap.query_adapter()(question, retrieval_mode, request_id)


_default = ServiceBootstrap()

query_adapter = _QueryAdapter(_default)


def configure_evaluation(config_path: Path) -> None:
    """평가 CLI의 설정 전달 훅. 일반·보안 callable이 같은 설정/자원을 공유한다."""
    _default.configure(config_path)


def retrieve_context(question, retrieval_mode):
    """보안 비교 CLI용. 검색·문맥 구성을 한 번 수행하고 같은 문맥을 두 변형에 재사용하게 보관한다."""
    retrieve_fn, _ = _default.security_adapters()
    return retrieve_fn(question, retrieval_mode)


def generate_with_context(question, final_context_chunks, prompt_variant):
    """보안 비교 CLI용. ``retrieve_context``가 보관한 문맥으로 base/defense 답변을 만든다."""
    _, generate_fn = _default.security_adapters()
    return generate_fn(question, final_context_chunks, prompt_variant)
