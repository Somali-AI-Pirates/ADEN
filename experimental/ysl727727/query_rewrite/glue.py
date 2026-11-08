"""C의 run_benchmark에 B의 서비스를 잇는 로컬 연결 코드 (개인 실험).

    QR_CONFIG   실험 설정 YAML 경로 (없으면 configs/basic.yaml)
    QR_ENABLE   "1"이면 hybrid_rerank 대신 질문 재작성 검색(retrieve_qr)을 쓴다

run_benchmark --adapter glue:adapter 로 쓴다. 저장소 루트에서 실행하고 PYTHONPATH에 이 폴더를 넣는다.
제품 코드(app/·shared/)는 바꾸지 않고, 실행 중인 Resources의 retriever만 바꿔 끼운다.
"""
import atexit
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)
load_dotenv(ROOT / ".env", override=False)

import json  # noqa: E402

from openai import OpenAI  # noqa: E402
from rewrite import llm_rewrite, retrieve_qr  # noqa: E402

from app.service.adapters import make_query_adapter  # noqa: E402
from app.service.resources import load_resources  # noqa: E402
from shared.settings import load_settings  # noqa: E402

_config = os.environ.get("QR_CONFIG")
_settings = load_settings(Path(_config)) if _config else load_settings()
USE_QR = os.environ.get("QR_ENABLE") == "1"
print(f"[glue] 설정 {_config or 'configs/basic.yaml'} | k={_settings.retrieval.k} "
      f"| 예산={_settings.retrieval.context_token_budget} | 질문 재작성={'사용' if USE_QR else '안 함'}", flush=True)
_resources = None


def _res():
    global _resources
    if _resources is None:
        _resources = load_resources(_settings)
        atexit.register(_resources.close)
        if USE_QR:
            base = _resources.retriever
            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=60, max_retries=1)
            cache_path = HERE / "results" / "llm_rewrites_runtime.json"
            cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

            def retriever(question, mode, resources, top_n=20):
                if mode != "hybrid_rerank":
                    return base(question, mode, resources, top_n=top_n)
                # LLM 재작성 + 리랭커 점수 합치기 (llm_fuse). 재작성 호출 시간도 응답 시간에 들어간다.
                if question not in cache:
                    try:
                        cache[question] = llm_rewrite(question, client, _settings.generation_model)
                    except Exception as e:
                        print(f"[glue] LLM 재작성 실패 {type(e).__name__}, 규칙 재작성으로 대신", flush=True)
                        cache[question] = None
                    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                return retrieve_qr(question, resources, top_n, generic=cache[question], fuse=True)

            _resources.retriever = retriever
    return _resources


class _Adapter:
    def __init__(self):
        self._fn = None

    @property
    def tracer(self):
        return _res().tracer

    def __call__(self, question, retrieval_mode, request_id):
        if self._fn is None:
            self._fn = make_query_adapter(_res(), _settings)
        return self._fn(question, retrieval_mode, request_id)


adapter = _Adapter()
