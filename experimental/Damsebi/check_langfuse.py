"""Langfuse에 실제로 저장된 trace와 평가 점수를 API로 조회해 로컬 기록과 대조한다. 키 값은 출력하지 않는다.

`verified`·`recorded_unverified`는 전송·존재 확인까지다. 내용과 점수 수신은 이 도구로 확인한다.

    python experimental/Damsebi/check_langfuse.py synthetic            # 합성 trace 1건 기록 후 조회
    python experimental/Damsebi/check_langfuse.py inspect <trace_id>…  # 기존 trace 조회
    python experimental/Damsebi/check_langfuse.py runs <runs.jsonl>    # 실행 기록과 저장 내용 대조
    python experimental/Damsebi/check_langfuse.py scores <채점 실험 폴더> [<원본 runs.jsonl>]
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.evaluation.publishing import GROUPS, expected_observations, score_items  # noqa: E402
from app.observability.tracing import Tracer  # noqa: E402

# 재생성이 없으면 generation-attempt-2 이후는 생기지 않는다. 시도 수는 실행 기록과 대조한다.
REQUIRED = ("rag-query", "retrieval", "final-context", "generation-attempt-1")
FIELDS = "core,basic,time,io,metadata,model,usage"
SLACK_MS = 2000  # 관측 구간과 기록된 시간의 허용 차이


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


def duration_ms(d):
    if not d.get("startTime") or not d.get("endTime"):
        return None
    return round((d["endTime"] - d["startTime"]).total_seconds() * 1000)


def fetch(client, trace_id, expected=REQUIRED, timeout_s=120):
    """기대 관측이 모두 보일 때까지 조회한다(수집 지연)."""
    deadline = time.monotonic() + timeout_s
    while True:
        data = client.api.observations.get_many(trace_id=trace_id, limit=50, fields=FIELDS).data
        names = {o.name for o in data}
        if all(n in names for n in expected) or time.monotonic() >= deadline:
            return data
        time.sleep(5)


def inspect(client, trace_id, run=None):
    staged = run is not None and "provided_context" in run  # PR #40 이후 기록: 실제 구간으로 남는다
    # 오류로 끝난 요청은 실행 기록에 남은 단계까지만 기대한다
    expected = list(REQUIRED) if run is None else expected_observations(run)
    if staged:
        expected += [f"validation-attempt-{a['attempt']}" for a in run["attempts"]
                     if a.get("model_output") is not None]
    obs = fetch(client, trace_id, expected)
    by_name = {o.name: o for o in obs}
    ids = {o.id: o.name for o in obs}
    root = by_name.get("rag-query")
    print(f"\ntrace {trace_id}: 관측 {len(obs)}개 {sorted(by_name)}")
    problems = [f"기대 관측 없음: {n}" for n in expected if n not in by_name]
    spans = {}
    for o in sorted(obs, key=lambda o: str(o.start_time)):
        d = o.dict()
        spans[o.name] = duration_ms(d)
        parent = ids.get(d.get("parentObservationId"))
        want = "generation-attempt-" + o.name.rsplit("-", 1)[-1] if o.name.startswith("validation-attempt-") else "rag-query"
        if o is not root and parent != want:
            problems.append(f"{o.name}의 상위 관측이 {want}가 아님({parent})")
        print(f"  - {o.name:<22} type={d.get('type'):<10} parent={parent or '-':<21} {spans[o.name]}ms"
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
        if not cands and (run is None or run.get("retrieved_candidates")):
            problems.append("retrieval 후보 없음")
        if run is not None and len(cands) != len(run.get("retrieved_candidates") or []):
            problems.append("후보 수가 실행 기록과 다름")
        if staged:
            if (as_obj(ret.input) or {}).get("question") != run["question"]:
                problems.append("retrieval 입력의 질문이 실행 기록과 다름")
            if (ret.dict().get("metadata") or {}).get("provided_context") != run["provided_context"]:
                problems.append("retrieval의 provided_context가 실행 기록과 다름")
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
        if staged:
            if out.get("model_context_token_count") != run["model_context_token_count"]:
                problems.append("문맥 토큰 수가 실행 기록과 다름")
            if out.get("budget_excluded_chunk_ids") != run["budget_excluded_chunk_ids"]:
                problems.append("예산 제외 ID가 실행 기록과 다름")
    gens = [o for o in obs if (o.name or "").startswith("generation-attempt-")]
    usages = [g.dict().get("usageDetails") or {} for g in gens]
    failed = {f"generation-attempt-{a['attempt']}" for a in (run or {}).get("attempts", [])
              if a.get("model_output") is None}  # 모델 호출 자체가 실패한 시도는 출력·사용량이 없다
    for g, usage in zip(gens, usages):
        d = g.dict()
        if g.name in failed:
            if d.get("level") != "ERROR":
                problems.append(f"{g.name} 실패 시도가 ERROR로 기록되지 않음")
            continue
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
        if (lf_in, lf_out) != (rec["input_tokens"] or 0, rec["output_tokens"] or 0):
            problems.append("토큰 사용량이 실행 기록과 다름")
    if staged:
        # 관측 구간이 실제 실행 시간과 맞는지: 생성 구간은 시도별 latency_ms, 루트는 요청 latency_ms
        pairs = [(f"generation-attempt-{a['attempt']}", a.get("latency_ms")) for a in run["attempts"]]
        pairs.append(("rag-query", run.get("latency_ms")))
        for name, recorded in pairs:
            span = spans.get(name)
            print(f"    구간 {name}: Langfuse {span}ms · 기록 {recorded}ms")
            if span is None or recorded is None or span + 50 < recorded or span - recorded > SLACK_MS:
                problems.append(f"{name} 구간이 기록된 시간과 맞지 않음")
        inner = sum(spans.get(n) or 0 for n in spans if n != "rag-query" and not n.startswith("validation-"))
        if spans.get("rag-query") is not None and inner > spans["rag-query"] + 50:
            problems.append("하위 구간 합이 루트 구간보다 김")
    print("    결과:", "필수 관측·내용 확인" if not problems else f"문제 {problems}")
    return not problems


def remote_scores(client, **filters):
    rows, cursor = [], None
    while True:
        page = client.api.scores.get_many(limit=100, cursor=cursor, fields="details,subject", **filters)
        rows += page.data
        cursor = page.meta.cursor
        if not cursor:
            return rows


def check_scores(client, exp_dir, runs_path=None, timeout_s=120):
    """게시한 점수가 서버에 있고 이름·값·trace·metadata가 로컬 채점 기록과 같으며 중복이 없는지 확인한다."""
    exp_dir = Path(exp_dir)
    manifest = json.loads((exp_dir / "manifest.json").read_text(encoding="utf-8"))
    read = lambda p: [json.loads(line) for line in Path(p).read_text(encoding="utf-8").splitlines() if line.strip()]
    runs = {r["request_id"]: r for r in read(runs_path or exp_dir / "runs.jsonl")}
    published = manifest.get("langfuse_scores") or {}
    # 평가 세트 도입 전 게시는 이름에 세트가 없고 같은 이름이 trace에 쌓여 있다
    prefix = f"{published['evaluation_set']}." if published.get("evaluation_set") else ""
    groups = published.get("groups", GROUPS)
    print(f"게시 기록: {published.get('status')} 세트 {published.get('evaluation_set')} "
          f"지표군 {groups} {published.get('score_status_counts')}")
    ok, totals, local = True, Counter(), {}
    for row in read(exp_dir / "scores.jsonl"):
        trace_id = runs[row["request_id"]]["langfuse_trace_id"]
        sent = row.get("langfuse_score_ids") or {}
        items = {prefix + name: item for name, item in score_items(row, groups).items()}
        deadline = time.monotonic() + timeout_s
        while True:
            remote = {s.id: s for s in remote_scores(client, trace_id=trace_id)}
            if all(i in remote for i in sent.values()) or time.monotonic() >= deadline:
                break
            time.sleep(5)
        problems = []
        for name, score_id in sent.items():
            got = remote.get(score_id)
            if got is None:
                problems.append(f"{name} 미수신")
                continue
            d = got.dict()
            subject, meta = d.get("subject") or {}, d.get("metadata") or {}
            if (got.name != name or (subject.get("kind"), subject.get("id")) != ("trace", trace_id)
                    or abs(got.value - items[name]["value"]) > 1e-9):
                problems.append(f"{name} 불일치: 서버 {got.name}={got.value} {subject}")
            expected = {"experiment_id": manifest["experiment_id"]}
            if prefix:
                expected.update(evaluation_set=published["evaluation_set"],
                                evaluation_hash=published["evaluation_hash"],
                                source_experiment_id=(manifest.get("regrade") or {}).get(
                                    "source_experiment_id") or manifest["experiment_id"],
                                **{key: (manifest["benchmark"].get(key)) for key in
                                   ("judge", "judge_prompt", "references_sha256")},
                                config_hash=manifest["config_hash"])
            wrong = {k: meta.get(k) for k, v in expected.items() if meta.get(k) != v}
            if wrong:
                problems.append(f"{name}의 metadata가 다름: {wrong}")
            local.setdefault(name, []).append(items[name]["value"])
        # 채점되지 않은 항목(해당 없음·평가 불가)은 보내지 않았어야 한다
        unsent = [n for n, item in items.items() if item["status"] != "scored"]
        if set(unsent) & set(sent):
            problems.append(f"채점되지 않은 항목 게시: {sorted(set(unsent) & set(sent))}")
        names = Counter(s.name for s in remote.values())
        if prefix:  # 이 세트로 이 trace에 온 점수는 보낸 것과 정확히 같아야 한다(중복·고르지 않은 지표군 없음)
            in_set = sorted(n for n in names.elements() if n.startswith(prefix))
            if in_set != sorted(sent):
                problems.append(f"세트의 서버 점수가 보낸 것과 다름: {in_set}")
        totals.update(received=len(sent) - sum("미수신" in p for p in problems), sent=len(sent), unsent=len(unsent))
        print(f"  {row['question_id']} {row['retrieval_mode']:<13} trace {trace_id[:8]}… 게시 {len(sent)} · 미게시 {len(unsent)}"
              f" · 이 trace의 전체 점수 {len(remote)}개(이름별 최대 {max(names.values(), default=0)}개)"
              f" → {'일치' if not problems else problems}")
        ok &= not problems
    print(f"합계: 게시 {totals['sent']} · 수신 확인 {totals['received']} · 미게시(채점 안 됨) {totals['unsent']}")
    if prefix:  # 이름으로 조회하면 이 세트의 점수만 나오는지: Langfuse의 이름별 선택·집계가 보는 범위
        for name, values in sorted(local.items()):
            got = remote_scores(client, name=name)
            hashes = {(s.dict().get("metadata") or {}).get("evaluation_hash") for s in got}
            mean = sum(s.value for s in got) / len(got) if got else None
            fine = len(got) >= len(values) and hashes == {published["evaluation_hash"]}
            print(f"  이름 조회 {name}: 서버 {len(got)}건 평균 {mean} · 이 실험 {len(values)}건 평균 "
                  f"{sum(values) / len(values)} · 채점 조건 {len(hashes)}종 → {'일치' if fine else '문제'}")
            ok &= fine
    return ok


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
    elif mode == "scores":
        ok = check_scores(client, *sys.argv[2:4])
    else:
        lines = Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
        runs = [json.loads(line) for line in lines if line.strip()]
        ok = all([inspect(client, r["langfuse_trace_id"], r) for r in runs])
    print("\n전체:", "통과" if ok else "문제 있음")
    tracer.shutdown()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
