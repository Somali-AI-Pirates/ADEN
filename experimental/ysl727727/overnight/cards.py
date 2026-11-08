"""문서 카드: 문서마다 LLM 1회로 '문서 종류·다루는 범위·핵심 용어'를 200자 안팎으로 만든다 (정답 파일 사용 안 함).

청크마다 이 카드를 앞에 붙이면(index.py의 c800 변형) 리랭커·BM25·임베딩이 "이 청크가 보관료 규칙 문서의 일부"인지
알 수 있다. 일반 요약이 아니라 다른 문서와 구별되는 내용을 쓰게 한다(Anthropic·SAC 조사 결과 반영).
    <gpu python> cards.py <level>
출력: results/doc_cards_<level>.json
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from common import HERE, OUT, ROOT, log, read_jsonl

PROMPT = ("다음은 사내 해운 업무 문서 하나의 제목, 절 제목 목록, 첫 쪽 본문이다. 검색용 문서 카드를 한국어 2~3문장, "
          "200자 이내로 써라. 반드시 포함: (1) 문서 종류(일반 규칙·기준 / 요율표 / 계약 조건 / 절차 안내 / 공지 / "
          "개별 기록·원장·대장 / 회신·정산 등 중 해당하는 것), (2) 이 문서가 정하거나 기록하는 대상과 범위(항로, 터미널, "
          "비용 항목, 기간 등), (3) 다른 문서와 구별되는 핵심 용어. 개별 예약번호·회사명은 넣지 마라. 카드 문장만 출력.")


def main(level):
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    client = OpenAI()
    docs = {}
    for p in read_jsonl(OUT / level / "pages.jsonl"):
        lines = [x.strip() for x in p["text"].splitlines() if x.strip()]
        d = docs.setdefault(p["book_id"], {"title": " / ".join(lines[:2]), "secs": {}, "first": ""})
        if len(lines) > 2:
            d["secs"].setdefault(lines[2][:50], None)
        if p["page"] == 1:
            d["first"] = "\n".join(lines[2:])[:1500]
    path = HERE / "results" / f"doc_cards_{level}.json"
    out = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def one(b):
        d = docs[b]
        user = f"제목: {d['title']}\n절 제목: {' / '.join(list(d['secs'])[:40])}\n첫 쪽 본문:\n{d['first']}"
        for _ in range(2):
            try:
                r = client.chat.completions.create(model="gpt-6-luna", max_completion_tokens=1500,
                                                   messages=[{"role": "system", "content": PROMPT},
                                                             {"role": "user", "content": user}])
                with open(HERE / "results" / "cost_ledger.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps({"tag": "cards", "in": r.usage.prompt_tokens,
                                        "out": r.usage.completion_tokens}) + "\n")
                text = (r.choices[0].message.content or "").strip()
                if text:
                    return b, text[:300]
            except Exception:  # noqa: BLE001
                continue
        return b, ""

    with ThreadPoolExecutor(8) as ex:
        for b, text in ex.map(one, [b for b in docs if not out.get(b)]):
            out[b] = text
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{level}: 문서 카드 {sum(bool(v) for v in out.values())}/{len(docs)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1])
