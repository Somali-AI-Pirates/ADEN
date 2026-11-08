"""자체 평가용 합성 질문 세트 (두 문서를 함께 봐야 답할 수 있는 질문 + 같은 근거의 거짓 전제 변형).

    <gpu python> synth.py <level> <쌍 개수>
만드는 법 (정답 파일 사용 안 함, PDF 페이지 텍스트만):
  1) 기준 페이지 A를 무작위로 고른다 (seed 고정).
  2) 짝 페이지 B: 절반은 A 본문으로 BM25 상위 5개 중 다른 문서, 절반은 다른 문서의 무작위 페이지.
  3) gpt-6-luna가 A·B를 모두 써야 답할 수 있는 질문 1개와 거짓 전제 변형 1개를 만든다. 불가능하면 none.
  4) 인용문이 원문(공백 무시)에 실제로 있는지 코드로 확인하고, 실패한 항목은 버린다.
출력: data/synth_<level>.json (전체), data/synth_<level>_questions.json (id·question만)
비용 기록: results/cost_ledger.jsonl
"""
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from common import HERE, OUT, ROOT, SparseBM25, log, read_jsonl

MODEL = "gpt-6-luna"
PROMPT = """너는 해운 업무 문서로 RAG 평가 문항을 만드는 출제자다. 아래 두 페이지(문서 A, 문서 B)를 읽고,
두 페이지의 정보를 **모두** 써야 답할 수 있는 실무 질문을 만들어라. 한 페이지만으로 답할 수 있으면 안 된다.
- 질문은 실무자가 메일로 묻는 자연스러운 한국어. 원문 문장을 그대로 베끼지 말고, 문서 ID(AS001, IS11 등)는 넣지 마라.
- 예약번호·장비번호·회사명·날짜처럼 질문 대상을 특정하는 정보는 필요하면 넣어도 된다.
- 같은 근거로 '그럴듯한 거짓 전제'가 들어간 변형 질문도 하나 만들어라(문서와 다른 날짜·일수·요율·담당·적용 범위 등을 사실처럼 전제).
- 각 근거 인용은 해당 페이지 원문에서 그대로 복사한 연속 구간(20~150자)이어야 한다.
- 두 페이지를 엮는 자연스러운 질문이 불가능하면 {"none": true}만 출력.
출력은 JSON 하나:
{"question": "...", "reference_answer": "...", "required_facts": ["..."],
 "quote_a": "...", "quote_b": "...",
 "fp_question": "...", "fp_reference_answer": "...", "fp_wrong_premise": "...", "fp_expected_action": "answer|abstain|clarify"}

[문서 A] {a_id} {a_page}쪽
{a_text}

[문서 B] {b_id} {b_page}쪽
{b_text}"""


def norm(s):
    return re.sub(r"\s+", "", s)


def ledger(tag, usage):
    with open(HERE / "results" / "cost_ledger.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"tag": tag, "in": usage.prompt_tokens, "out": usage.completion_tokens}) + "\n")


def main(level, n_pairs):
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    client = OpenAI()
    pages = read_jsonl(OUT / level / "pages.jsonl")
    body = lambda p: "\n".join(x for x in p["text"].splitlines()[:-1])[:1800]  # noqa: E731  끝줄(쪽 번호) 제외
    bm = SparseBM25([p["text"] for p in pages])
    rnd = random.Random(20261107)
    pairs = []
    for t in range(n_pairs):
        a = rnd.randrange(len(pages))
        if t % 2 == 0:
            cand = [i for i, _ in bm.search(body(pages[a])[:400], 30) if pages[i]["book_id"] != pages[a]["book_id"]][:5]
            b = rnd.choice(cand) if cand else rnd.randrange(len(pages))
        else:
            b = rnd.randrange(len(pages))
        if pages[a]["book_id"] != pages[b]["book_id"]:
            pairs.append((a, b, "bm25" if t % 2 == 0 else "random"))

    def one(job):
        a, b, how = job
        pa, pb = pages[a], pages[b]
        msg = PROMPT
        for k, v in {"{a_id}": pa["book_id"], "{a_page}": str(pa["page"]), "{a_text}": body(pa),
                     "{b_id}": pb["book_id"], "{b_page}": str(pb["page"]), "{b_text}": body(pb)}.items():
            msg = msg.replace(k, v)
        try:
            r = client.chat.completions.create(model=MODEL, max_completion_tokens=3000,
                                               messages=[{"role": "user", "content": msg}])
            ledger(f"synth_{level}", r.usage)
            raw = r.choices[0].message.content
            d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        except Exception as e:  # noqa: BLE001
            return None, f"error {type(e).__name__}"
        if d.get("none"):
            return None, "none"
        ok_a = norm(d.get("quote_a", "")) and norm(d["quote_a"]) in norm(pa["text"])
        ok_b = norm(d.get("quote_b", "")) and norm(d["quote_b"]) in norm(pb["text"])
        if not (ok_a and ok_b):
            return None, "quote_fail"
        ev = [[{"book_id": pa["book_id"], "page": pa["page"], "quote": d["quote_a"]}],
              [{"book_id": pb["book_id"], "page": pb["page"], "quote": d["quote_b"]}]]
        return {"pair": how, "evidence_groups": ev, **d}, "ok"

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(one, pairs))
    items, stats = [], {}
    for (res, st) in results:
        stats[st] = stats.get(st, 0) + 1
        if res:
            k = len(items) // 2 + 1
            base = {"evidence_groups": res["evidence_groups"], "grading_notes": f"합성({res['pair']}) 두 문서 근거"}
            items.append({"id": f"SY-{level}-{k:03d}", "question": res["question"],
                          "reference_answer": res["reference_answer"], "required_facts": res.get("required_facts", []),
                          "forbidden_claims": [], "expected_action": "answer", **base})
            items.append({"id": f"SYF-{level}-{k:03d}", "question": res["fp_question"],
                          "reference_answer": res["fp_reference_answer"],
                          "required_facts": [res.get("fp_reference_answer", "")[:80]],
                          "forbidden_claims": [res.get("fp_wrong_premise", "")],
                          "expected_action": res.get("fp_expected_action", "answer"), **base})
    (HERE / "data" / f"synth_{level}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "data" / f"synth_{level}_questions.json").write_text(
        json.dumps([{"id": x["id"], "question": x["question"]} for x in items], ensure_ascii=False, indent=1),
        encoding="utf-8")
    log(f"{level}: 쌍 {len(pairs)}개 → 문항 {len(items)}개 (정상+거짓전제) | {stats}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1], int(sys.argv[2]))
