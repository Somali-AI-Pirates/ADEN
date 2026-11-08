"""자체 평가용 다문서 합성 질문 (정답 문서 3개·4개가 모두 필요한 질문 + 같은 근거의 거짓 전제 변형).

    <gpu python> synth_multi.py <level> <문서 수별 시도 횟수>
만드는 법 (정답 파일 사용 안 함, PDF 페이지 텍스트만):
  1) 기준 페이지 A를 무작위로 고른다 (seed 고정).
  2) 나머지 페이지는 서로 다른 문서에서 고른다. 절반은 A 본문 BM25 상위의 다른 문서들(anchor),
     절반은 바로 앞 페이지 본문으로 BM25를 이어 가며 고른다(chain).
  3) gpt-6-luna가 n개 페이지를 모두 써야 답할 수 있는 질문 1개와 거짓 전제 변형 1개를 만든다. 불가능하면 none.
  4) 인용문이 각 페이지 원문(공백 무시)에 실제로 있는지 코드로 확인한다.
  5) 별도 호출(검사기)이 질문과 페이지들(순서 섞음, 문서 ID 없음)만 보고 '반드시 필요한 페이지'를 고른다.
     n개가 모두 필요하다고 판정된 문항만 남긴다 (정답 문서 수 부풀리기 방지).
출력: data/synth_multi_<level>.json (전체), data/synth_multi_<level>_questions.json (id·question만)
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
LETTERS = "ABCD"
GEN = """너는 해운 업무 문서로 RAG 평가 문항을 만드는 출제자다. 아래 {n}개 페이지(서로 다른 문서)를 읽고,
{n}개 페이지의 정보를 **모두** 써야 완전하게 답할 수 있는 실무 질문을 만들어라.
- 어느 한 페이지라도 빠지면 답이 틀리거나 불완전해야 한다. 페이지마다 답에 꼭 들어가는 서로 다른 사실이 있어야 한다.
- 질문은 실무자가 메일로 묻는 자연스러운 한국어. 여러 가지를 함께 확인하는 복합 질문이어도 좋다.
- 원문 문장을 그대로 베끼지 말고, 문서 ID(AS001, IS11 등)나 '문서 A' 같은 표현은 넣지 마라.
- 예약번호·장비번호·회사명·날짜처럼 질문 대상을 특정하는 정보는 필요하면 넣어도 된다.
- 같은 근거로 '그럴듯한 거짓 전제'가 들어간 변형 질문도 하나 만들어라(문서와 다른 날짜·일수·요율·담당·적용 범위 등을 사실처럼 전제).
- 각 페이지 인용은 그 페이지 원문에서 그대로 복사한 연속 구간(20~150자)이어야 한다.
- {n}개 페이지를 모두 엮는 자연스러운 질문이 불가능하면 {{"none": true}}만 출력.
출력은 JSON 하나:
{{"question": "...", "reference_answer": "...", "required_facts": ["..."],
 "quotes": {{{quote_keys}}},
 "fp_question": "...", "fp_reference_answer": "...", "fp_wrong_premise": "...", "fp_expected_action": "answer|abstain|clarify"}}

{pages}"""

CHECK = """아래 질문에 완전하고 정확하게 답하려면 다음 페이지 중 어느 것이 반드시 필요한지 판정하라.
'반드시 필요'는 그 페이지가 없으면 답의 일부가 틀리거나 빠지는 경우만이다. 다른 페이지로 대신 알 수 있으면 필요하지 않다.
출력은 JSON 하나: {{"needed": ["P1", ...], "reason": {{"P1": "한 줄", ...}}}}

[질문] {question}

{pages}"""


def norm(s):
    return re.sub(r"\s+", "", s)


def ledger(tag, usage):
    with open(HERE / "results" / "cost_ledger.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"tag": tag, "in": usage.prompt_tokens, "out": usage.completion_tokens}) + "\n")


def parse(raw):
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])


def main(level, attempts):
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    client = OpenAI()
    pages = read_jsonl(OUT / level / "pages.jsonl")
    body = lambda p, m=1500: "\n".join(p["text"].splitlines()[:-1])[:m]  # noqa: E731  끝줄(쪽 번호) 제외
    bm = SparseBM25([p["text"] for p in pages])
    usable = [i for i, p in enumerate(pages) if len(body(p)) >= 200]

    def neighbors(src, used_books, top):
        out, seen = [], set(used_books)
        for i, _ in bm.search(body(pages[src])[:400], 60):
            b = pages[i]["book_id"]
            if b not in seen:
                seen.add(b)
                out.append(i)
            if len(out) >= top:
                break
        return out

    jobs = []
    for n in (3, 4):
        rnd = random.Random(f"{level}-{n}-20261107")
        for t in range(attempts):
            a = rnd.choice(usable)
            picked = [a]
            mode = "anchor" if t % 2 == 0 else "chain"
            if mode == "anchor":
                cand = neighbors(a, {pages[a]["book_id"]}, 8)
                if len(cand) >= n - 1:
                    picked += rnd.sample(cand, n - 1)
            else:
                for _ in range(n - 1):
                    cand = neighbors(picked[-1], {pages[i]["book_id"] for i in picked}, 5)
                    if not cand:
                        break
                    picked.append(rnd.choice(cand))
            if len(picked) == n:
                jobs.append((n, mode, picked, random.Random(f"{level}-{n}-{t}").sample(range(n), n)))

    def one(job):
        n, mode, picked, perm = job
        ps = [pages[i] for i in picked]
        text = "\n\n".join(f"[페이지 {LETTERS[j]}] {p['book_id']} {p['page']}쪽\n{body(p)}" for j, p in enumerate(ps))
        keys = ", ".join(f'"{LETTERS[j]}": "..."' for j in range(n))
        try:
            r = client.chat.completions.create(
                model=MODEL, max_completion_tokens=4000,
                messages=[{"role": "user", "content": GEN.format(n=n, quote_keys=keys, pages=text)}])
            ledger(f"synthmulti_{level}", r.usage)
            d = parse(r.choices[0].message.content)
        except Exception as e:  # noqa: BLE001
            return None, f"error {type(e).__name__}"
        if d.get("none") or not d.get("question"):
            return None, "none"
        quotes = d.get("quotes") or {}
        for j, p in enumerate(ps):
            qt = norm(str(quotes.get(LETTERS[j], "")))
            if not qt or qt not in norm(p["text"]):
                return None, "quote_fail"
        # 검사기: 순서를 섞고 문서 ID 없이 페이지 본문만 준다.
        order = [ps[k] for k in perm]
        ctext = "\n\n".join(f"[P{j + 1}]\n{body(p)}" for j, p in enumerate(order))
        try:
            r = client.chat.completions.create(
                model=MODEL, max_completion_tokens=2000,
                messages=[{"role": "user", "content": CHECK.format(question=d["question"], pages=ctext)}])
            ledger(f"synthmulti_{level}", r.usage)
            c = parse(r.choices[0].message.content)
        except Exception as e:  # noqa: BLE001
            return None, f"check_error {type(e).__name__}"
        needed = {str(x).strip().upper() for x in c.get("needed") or []}
        if needed != {f"P{j + 1}" for j in range(n)}:
            return None, f"check_{len(needed & {f'P{j + 1}' for j in range(n)})}of{n}"
        ev = [[{"book_id": p["book_id"], "page": p["page"], "quote": quotes[LETTERS[j]]}] for j, p in enumerate(ps)]
        return {"n": n, "mode": mode, "evidence_groups": ev, **d}, "ok"

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(one, jobs))
    items, stats, count = [], {}, {3: 0, 4: 0}
    for res, st in results:
        stats[st] = stats.get(st, 0) + 1
        if not res:
            continue
        n = res["n"]
        count[n] += 1
        k = count[n]
        base = {"evidence_groups": res["evidence_groups"], "grading_notes": f"합성 다문서({n}개, {res['mode']})"}
        items.append({"id": f"SY{n}-{level}-{k:03d}", "question": res["question"],
                      "reference_answer": res["reference_answer"], "required_facts": res.get("required_facts", []),
                      "forbidden_claims": [], "expected_action": "answer", **base})
        items.append({"id": f"SYF{n}-{level}-{k:03d}", "question": res["fp_question"],
                      "reference_answer": res["fp_reference_answer"],
                      "required_facts": [res.get("fp_reference_answer", "")[:80]],
                      "forbidden_claims": [res.get("fp_wrong_premise", "")],
                      "expected_action": res.get("fp_expected_action", "answer"), **base})
    (HERE / "data" / f"synth_multi_{level}.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "data" / f"synth_multi_{level}_questions.json").write_text(
        json.dumps([{"id": x["id"], "question": x["question"]} for x in items], ensure_ascii=False, indent=1),
        encoding="utf-8")
    log(f"{level}: 시도 {len(jobs)}개 → 3문서 {count[3]}·4문서 {count[4]}건 (각 정상+거짓전제) | {stats}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1], int(sys.argv[2]))
