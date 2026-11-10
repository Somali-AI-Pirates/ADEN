"""Langfuse에 저장된 trace의 필수 관측·입출력·문맥·사용량을 API로 조회해 확인한다. 키 값은 출력하지 않는다.

`run_benchmark`의 `verified`는 관측이 하나만 조회돼도 나오므로, 내용 확인이 필요할 때 쓴다.

    python experimental/Damsebi/check_langfuse.py synthetic            # 합성 trace 1건 기록 후 조회
    python experimental/Damsebi/check_langfuse.py inspect <trace_id>…  # 기존 trace 조회
    python experimental/Damsebi/check_langfuse.py runs <runs.jsonl>    # 실행 기록과 저장 내용 대조
"""
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.observability.tracing import Tracer  # noqa: E402

# 재생성이 없으면 generation-attempt-2 이후는 생기지 않는다. 시도 수는 실행 기록과 대조한다.
REQUIRED = ("rag-query", "retrieval", "final-context", "generation-attempt-1")
FIELDS = "core,basic,io,metadata,model,usage"


def size(value):
    if value is None:
        return 0
    return len(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


def as_obj(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def fetch(client, trace_id, timeout_s=120):
    """필수 관측이 모두 보일 때까지 조회한다(수집 지연)."""
    deadline = time.monotonic() + timeout_s
    while True:
        data = client.api.observations.get_many(trace_id=trace_id, limit=50, fields=FIELDS).data
        names = {o.name for o in data}
        if all(n in names for n in REQUIRED) or time.monotonic() >= deadline:
            return data
        time.sleep(5)


def inspect(client, trace_id, run=None):
    obs = fetch(client, trace_id)
    by_name = {o.name: o for o in obs}
    root = by_name.get("rag-query")
    print(f"\ntrace {trace_id}: 관측 {len(obs)}개 {sorted(by_name)}")
    problems = [f"필수 관측 없음: {n}" for n in REQUIRED if n not in by_name]
    for o in sorted(obs, key=lambda o: str(o.start_time)):
        d = o.dict()
        parent = "root" if o is root else d.get("parentObservationId") == getattr(root, "id", None)
        if parent is False:
            problems.append(f"{o.name}의 상위 관측이 rag-query가 아님")
        print(f"  - {o.name:<22} type={d.get('type'):<10} parent_ok={parent}"
              f" input={size(d.get('input'))}자 output={size(d.get('output'))}자"
              f" model={d.get('model')} usage={d.get('usageDetails') or {}} level={d.get('level')}")
    if root is not None:
        rin, rout = as_obj(root.input) or {}, as_obj(root.output) or {}
        if not rin.get("question"):
            problems.append("rag-query 입력에 질문 없음")
        if not rout.get("status"):
            problems.append("rag-query 출력에 status 없음")
        if run is not None:
            if rin.get("question") != run["question"]:
                problems.append("질문이 실행 기록과 다름")
            if rout.get("answer") != run.get("answer") or rout.get("status") != run["status"]:
                problems.append("답변·상태가 실행 기록과 다름")
    ret, ctx = by_name.get("retrieval"), by_name.get("final-context")
    if ret is not None:
        cands = (as_obj(ret.output) or {}).get("candidates") or []
        print(f"    retrieval 후보 {len(cands)}개")
        if not cands:
            problems.append("retrieval 후보 없음")
        if run is not None and len(cands) != len(run.get("retrieved_candidates") or []):
            problems.append("후보 수가 실행 기록과 다름")
    if ctx is not None:
        out = as_obj(ctx.output) or {}
        chunks = out.get("final_context_chunks") or []
        print(f"    final-context 청크 {len(chunks)}개, model_context_text {size(out.get('model_context_text'))}자,"
              f" hash={str(out.get('final_context_hash'))[:19]}")
        if not chunks or not out.get("model_context_text"):
            problems.append("final-context에 청크 또는 문맥 문자열 없음")
        if run is not None:
            if out.get("final_context_hash") != run.get("final_context_hash"):
                problems.append("문맥 해시가 실행 기록과 다름")
            if [c.get("chunk_id") for c in chunks] != [c["chunk_id"] for c in run["final_context_chunks"]]:
                problems.append("문맥 청크 ID가 실행 기록과 다름")
    gens = [o for o in obs if (o.name or "").startswith("generation-attempt-")]
    usages = [g.dict().get("usageDetails") or {} for g in gens]
    for g, usage in zip(gens, usages):
        d = g.dict()
        if not size(d.get("input")) or not size(d.get("output")):
            problems.append(f"{g.name} 입출력 없음")
        if not usage.get("input") or not usage.get("output"):
            problems.append(f"{g.name} 사용량 없음")
    if run is not None:
        if len(gens) != len(run["attempts"]):
            problems.append(f"생성 시도 수 불일치: Langfuse {len(gens)} / 기록 {len(run['attempts'])}")
        lf_in = sum(u.get("input") or 0 for u in usages)
        lf_out = sum(u.get("output") or 0 for u in usages)
        rec = run["usage"]
        print(f"    토큰 Langfuse {lf_in}/{lf_out} · 실행 기록 {rec['input_tokens']}/{rec['output_tokens']}")
        if (lf_in, lf_out) != (rec["input_tokens"], rec["output_tokens"]):
            problems.append("토큰 사용량이 실행 기록과 다름")
    print("    결과:", "필수 관측·내용 확인" if not problems else f"문제 {problems}")
    return not problems


def synthetic(tracer):
    request_id = f"langfuse-synthetic-check-{int(time.time())}"
    t = tracer.start_request(request_id, question="[합성 점검] 연결 확인용 질문", retrieval_mode="naive",
                             dataset_id="synthetic", config_hash="synthetic-check",
                             metadata={"purpose": "connection-check"})
    chunk = {"chunk_id": "SYN-p1-c1", "book_id": "SYN", "file_name": "SYN.pdf", "page": 1,
             "heading": "합성 제목", "text": "합성 문맥 본문", "source_id": "S1"}
    t.record_retrieval(retrieved_candidates=[{**chunk, "rank": 1, "score": 0.5, "retriever": "dense"}],
                       final_context_chunks=[chunk], final_context_hash="sha256:synthetic",
                       k=8, latency_ms=1, model_context_text="[S1] 합성 문맥 본문",
                       model_context_hash="synthetic")
    t.record_attempt(attempt=1, model="synthetic-model",
                     model_input=[{"role": "user", "content": "합성 입력"}],
                     model_output={"status": "answered", "answer": "합성 답변"},
                     input_tokens=11, output_tokens=7)
    done = t.finish(status="answered", answer="합성 답변", reason=None, citations=[], latency_ms=2)
    print("request_id:", request_id)
    print("finish:", done)
    print("flush:", tracer.flush())
    print("verify_trace:", tracer.verify_trace(done["langfuse_trace_id"], timeout_s=120))
    return done["langfuse_trace_id"]


def main():
    tracer = Tracer.from_env()
    if not tracer.enabled:
        raise SystemExit(".env에 LANGFUSE_PUBLIC_KEY·LANGFUSE_SECRET_KEY가 없다")
    client = tracer._client
    mode = sys.argv[1]
    if mode == "synthetic":
        ok = inspect(client, synthetic(tracer))
    elif mode == "inspect":
        ok = all([inspect(client, t) for t in sys.argv[2:]])
    else:
        lines = Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
        runs = [json.loads(line) for line in lines if line.strip()]
        ok = all([inspect(client, r["langfuse_trace_id"], r) for r in runs])
    print("\n전체:", "통과" if ok else "문제 있음")
    tracer.shutdown()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
