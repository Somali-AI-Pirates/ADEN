"""LLM 질문 재작성·분해 캐시 (질문 문장만 입력, 정답 파일 사용 안 함).

    <gpu python> qr.py <level> <kind>      kind = generic | decomp
출력: results/qr_<kind>_<level>.json  {qid: 문자열 또는 하위 질의 목록}
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from common import HERE, ROOT, log, questions

RES = HERE / "results"
MODEL = "gpt-6-luna"

GENERIC = ("너는 문서 검색용 질문 재작성기다. 사용자의 질문에서 특정 예약을 가리키는 구체 조건(날짜, 시각, 장비 코드, 대수, "
           "항로, 회사명, 번호)을 빼고, 질문이 실제로 묻는 업무 규칙이나 절차를 한국어 한 문장의 일반 질문으로 바꿔라. "
           "질문에 없는 사실이나 답을 덧붙이지 마라. 여러 가지를 물으면 핵심 규칙을 모두 담되 한 문장으로 쓴다. "
           "재작성한 질문 한 문장만 출력한다.")
DECOMP = ("너는 사내 업무 문서 검색용 질의 분해기다. 사용자의 질문에 답하려면 서로 다른 문서나 절에서 찾아야 할 정보가 "
          "여러 개일 수 있다. 질문을 검색 질의 1~4개로 나눠라. 규칙: 각 질의는 한국어 한 문장, 질문에 나온 식별자(예약번호, "
          "장비번호, 회사명, 날짜, 화물)는 필요한 질의에 그대로 유지, 질문에 없는 사실·숫자·답을 추측해 넣지 말 것, "
          "한 가지만 묻는 질문이면 1개만. 출력은 JSON 문자열 배열 하나만.")
# decomp2: 개별 기록 질의 + 그 판단에 적용할 일반 규칙·기준·요율·계약 조건을 찾는 질의(식별자 없이)를 함께 만든다.
DECOMP2 = ("너는 사내 업무 문서 검색용 질의 분해기다. 문서는 (1) 일반 규칙·기준·요율표·계약 조건·절차 안내 문서와 "
           "(2) 개별 예약·장비·청구 기록 문서로 나뉜다. 질문에 답하는 데 필요한 정보를 찾도록 검색 질의 2~5개를 만들어라. "
           "규칙: (a) 특정 기록(예약번호, 장비번호, 회사명, 날짜, 화물)을 찾는 질의는 그 식별자를 그대로 유지한다. "
           "(b) 그 판단·계산에 적용되는 일반 규칙·기준·요율·계약 조건·예외를 찾는 질의를 식별자 없이 1~2개 반드시 포함한다. "
           "(c) 질문에 없는 사실·숫자·답을 추측해 넣지 않는다. 각 질의는 한국어 한 문장. 출력은 JSON 문자열 배열 하나만.")


# kind 뒤에 붙는 꼬리: _none/_low/_medium/_high = gpt-6-luna 추론 강도, _qwen4b/_gemma4b = Ollama 로컬 모델
EFFORTS = ("none", "low", "medium", "high")
LOCAL = {"qwen4b": "qwen3:4b-instruct-2507-q4_K_M", "gemma4b": "gemma3:4b-it-q4_K_M"}
OLLAMA_URL = "http://localhost:11434/v1"


def split_kind(kind):
    """'decomp_none' → ('decomp', 'none', None) / 'decomp_qwen4b' → ('decomp', None, 'qwen3:...')."""
    base, _, tail = kind.partition("_")
    if tail in EFFORTS:
        return base, tail, None
    if tail in LOCAL:
        return base, None, LOCAL[tail]
    return kind, None, None


def make_client(local_model=None):
    from openai import OpenAI

    if local_model:
        return OpenAI(base_url=OLLAMA_URL, api_key="ollama")
    load_dotenv(ROOT / ".env")
    assert os.environ.get("OPENAI_API_KEY"), "OPENAI_API_KEY 없음"
    return OpenAI()


def ask(client, system, q, effort=None, model=None):
    extra = {"reasoning_effort": effort} if effort else {}
    tag = "qr_local" if model else (f"qr_{effort}" if effort else "qr")
    for _ in range(2):
        try:
            r = client.chat.completions.create(model=model or MODEL, max_completion_tokens=2000,
                                               messages=[{"role": "system", "content": system},
                                                         {"role": "user", "content": q}], **extra)
            with open(RES / "cost_ledger.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"tag": tag, "in": r.usage.prompt_tokens, "out": r.usage.completion_tokens}) + "\n")
            return r.choices[0].message.content.strip()
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:120]
    return {"error": err}


def main(level, kind, extra=()):
    base, effort, local_model = split_kind(kind)
    client = make_client(local_model)
    path = RES / f"qr_{kind}_{level}.json"
    out = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    qs = [q for q in questions(level, extra) if q["id"] not in out]
    system = {"generic": GENERIC, "decomp": DECOMP, "decomp2": DECOMP2}[base]

    def one(q):
        raw = ask(client, system, q["question"], effort=effort, model=local_model)
        if isinstance(raw, dict):
            return q["id"], None, raw["error"]
        if base == "generic":
            return q["id"], raw.splitlines()[0].strip(), None
        try:
            subs = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
            # shortcut: 하위 질의는 최대 4개, 정답 문서가 그보다 많은 질문은 놓칠 수 있음
            return q["id"], [s for s in subs if isinstance(s, str) and s.strip()][:4], None
        except Exception:  # noqa: BLE001
            return q["id"], None, f"parse: {raw[:80]}"

    errors = 0
    with ThreadPoolExecutor(2 if local_model else 8) as ex:
        for qid, val, err in ex.map(one, qs):
            if err:
                errors += 1
                log(f"  실패 {qid}: {err}")
            else:
                out[qid] = val
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{level} {kind}: 새로 {len(qs) - errors}개, 실패 {errors}, 전체 {len(out)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    level, kind = sys.argv[1:3]
    extra = [HERE / "data" / f"false_premise_{level}_questions.json"] if len(sys.argv) > 3 else []
    if len(sys.argv) > 3 and sys.argv[3] == "all":
        extra.append(HERE / "data" / f"synth_{level}_questions.json")
    if len(sys.argv) > 3 and sys.argv[3] == "multi":
        extra = [HERE / "data" / f"synth_multi_{level}_questions.json"]
    main(level, kind, extra)
