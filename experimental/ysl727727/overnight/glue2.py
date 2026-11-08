"""C의 run_benchmark에 B의 답변 생성을 잇고, 검색만 로컬 실험 파이프라인으로 바꿔 끼우는 연결 코드 (개인 실험).

    OV_LEVEL  basic | inter | adv
    OV_CFG    run.py 설정 이름 (base = main hybrid_rerank 재현, 예: base+graph6+mmr0.2+mq_decomp)
    OV_K      최종 문맥 청크 수 (기본 8),  OV_BUDGET 문맥 토큰 예산 (기본 8000)

    run_benchmark --adapter glue2:adapter --modes hybrid_rerank ...  (PYTHONPATH에 이 폴더)
제품 코드(app/·shared/)는 바꾸지 않는다. 실행 중인 프로세스에서 app.service.query.retrieve만 교체한다.
Qdrant Cloud에는 접속하지 않는다(로컬 GPU 색인). 하위 질의(mq_decomp)는 실행 중 LLM으로 만든다(캐시 재사용).
"""
import atexit
import json
import os
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)
load_dotenv(ROOT / ".env", override=False)

import app.service.query as query_mod  # noqa: E402
import run as ov  # noqa: E402
from app.service.adapters import make_query_adapter  # noqa: E402
from app.service.resources import Resources, _create_generation_model, _create_token_counter  # noqa: E402
from index import embed_queries  # noqa: E402
from shared.contracts import Chunk, RetrievedChunk  # noqa: E402
from shared.settings import load_settings  # noqa: E402

LEVEL = os.environ.get("OV_LEVEL", "basic")
CFG_NAME = os.environ.get("OV_CFG", "base")
K = int(os.environ.get("OV_K", "8"))
BUDGET = int(os.environ.get("OV_BUDGET", "8000"))
CFG = ov.preset(CFG_NAME)

_base = load_settings()
SETTINGS = _base.model_copy(update={
    "dataset": LEVEL,
    "qdrant_collection": f"local-{LEVEL}-{CFG['chunks']}-{CFG['emb']}",
    "retrieval": _base.retrieval.model_copy(update={"default_mode": "hybrid_rerank", "k": K,
                                                    "context_token_budget": BUDGET}),
})
print(f"[glue2] {LEVEL} | 검색 {CFG_NAME} | k={K} | 예산={BUDGET}", flush=True)

_state = {}
# 분해 종류마다 실행 중 캐시를 따로 둔다(decomp는 기존 파일 이름 유지, decomp_none 등은 꼬리를 붙임)
_sub_cache_path = HERE / "results" / (f"qr_decomp_runtime_{LEVEL}.json" if CFG["mq"] in (None, "decomp")
                                      else f"qr_{CFG['mq']}_runtime_{LEVEL}.json")


def _decomp(question):
    """질문 문장 → 하위 질의. 같은 질문은 미리 만든 결과(qr_decomp_<level>.json)를 재사용하고, 없으면 LLM 호출."""
    if "subs" not in _state:
        cache = json.loads(_sub_cache_path.read_text(encoding="utf-8")) if _sub_cache_path.exists() else {}
        pre = json.loads((HERE / "results" / f"qr_{CFG['mq']}_{LEVEL}.json").read_text(encoding="utf-8"))
        extra = [HERE / "data" / f"false_premise_{LEVEL}_questions.json"]
        if (HERE / "data" / f"synth_{LEVEL}_questions.json").exists():
            extra.append(HERE / "data" / f"synth_{LEVEL}_questions.json")
        for q in ov.questions(LEVEL, extra):
            if q["id"] in pre:
                cache.setdefault(q["question"], pre[q["id"]])
        _state["subs"] = cache
    cache = _state["subs"]
    if question not in cache:
        import qr

        base, effort, local_model = qr.split_kind(CFG["mq"])
        raw = qr.ask(qr.make_client(local_model), qr.DECOMP2 if base == "decomp2" else qr.DECOMP, question,
                     effort=effort, model=local_model)
        try:
            cache[question] = json.loads(raw[raw.index("["): raw.rindex("]") + 1])[:4]
        except Exception:  # noqa: BLE001
            cache[question] = []
        _sub_cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return [s for s in cache[question] if isinstance(s, str) and s.strip() and s.strip() != question.strip()]


def local_retrieve(question, mode, resources, top_n=20):
    if "ix" not in _state:
        _state["ix"] = ov.get_index(LEVEL, CFG)
        _state["rr"] = ov.Reranker(LEVEL) if CFG["rerank"] else None
        atexit.register(lambda: _state["rr"] and _state["rr"].save())
    ix, rr = _state["ix"], _state["rr"]
    t = time.perf_counter()
    qv = embed_queries([question], CFG["emb"])[0]
    subs = []
    if CFG["mq"] and CFG["mq"].startswith("decomp"):
        texts = _decomp(question)
        subs = list(zip(texts, embed_queries(texts, CFG["emb"]))) if texts else []
    order = ov.retrieve(ix, rr, CFG, question, qv, out_n=max(top_n, CFG["llmsel"]), subs=subs)
    if CFG["llmsel"]:
        import sel
        from openai import OpenAI

        order = ov.llm_select_one(_state.setdefault("client", OpenAI()), ix, CFG, question, order)[:top_n]
        sel.save()
    _state["last_ms"] = (time.perf_counter() - t) * 1000
    out = []
    for rank, i in enumerate(order, start=1):
        r = ix.rows[i]
        chunk = Chunk(chunk_id=r["chunk_id"], book_id=r["book_id"], file_name=f"{r['book_id']}.pdf",
                      page=r["page"], text=r["text"], heading=r["heading"])
        out.append(RetrievedChunk(chunk=chunk, rank=rank, score=float(len(order) - rank), retriever=CFG_NAME[:40]))
    return out


# shortcut: 실행 중 검색 함수만 바꿔 끼움, 제품에 반영할 때는 app/retrieval에 정식 검색 모드로 추가
query_mod.retrieve = local_retrieve

if os.environ.get("OV_FIXCITE") == "1":
    # shortcut: B의 출처 ID 검증 오류를 실험에서만 우회, B가 형식을 고치면 이 블록 삭제
    # 실험 전용 보정: 모델이 source_ids에 [S#] 대신 문서 ID(AS027 등)를 적으면 B의 검증이 ValueError로
    # 두 번 모두 실패한다. 그 문서의 S#로 바꿔 준다(본문 표시 중 같은 문서 것 우선, 없으면 그 문서 전체).
    # 원래 검증을 통과하는 응답은 바뀌지 않는다.
    import app.generation.chain as chain_mod

    _orig_validate = chain_mod._validate

    def _validate_fixcite(result, context):
        parsed = result.get("parsed")
        if parsed is not None and any(s not in context.source_map for s in parsed.source_ids):
            marked = chain_mod.CITATION_MARK.findall(parsed.answer)
            fixed = []
            for s in parsed.source_ids:
                if s in context.source_map:
                    fixed.append(s)
                    continue
                same = [m for m in marked if m in context.source_map and context.source_map[m].book_id == s]
                fixed.extend(same or [k for k, c in context.source_map.items() if c.book_id == s])
            result = {**result, "parsed": parsed.model_copy(update={"source_ids": list(dict.fromkeys(fixed))})}
        return _orig_validate(result, context)

    chain_mod._validate = _validate_fixcite
    print("[glue2] 출처 ID 보정 켬 (문서 ID → S#)", flush=True)

_resources = None


def _res():
    global _resources
    if _resources is None:
        cleanup = ExitStack()
        _resources = Resources(embedder=None, qdrant=None, collection=SETTINGS.qdrant_collection,
                               model=_create_generation_model(SETTINGS, cleanup),
                               count_tokens=_create_token_counter(SETTINGS.context_tokenizer),
                               cleanup=cleanup, tracer=None)
        atexit.register(_resources.close)
    return _resources


class _Adapter:
    def __init__(self):
        self._fn = None

    @property
    def tracer(self):
        return None

    def __call__(self, question, retrieval_mode, request_id):
        if self._fn is None:
            self._fn = make_query_adapter(_res(), SETTINGS)
        return self._fn(question, retrieval_mode, request_id)


adapter = _Adapter()
