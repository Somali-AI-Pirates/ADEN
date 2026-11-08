"""고급 합성 질문 중 '어느 예약인지 특정할 수 없는' 문항 표시 (정답·근거는 쓰지 않고 질문 문장과 문서 머리만 사용).

인계철(HS) 페이지 머리에서 예약별 (화주, 화물, 상자 수)를 모은 뒤, 질문에 나온 화주·화물·상자 수와 맞는 예약이
화주마다 정확히 1건으로 좁혀지지 않으면 모호하다고 본다. 예약번호(HG/HL)가 있는 질문은 모호하지 않다.
출력: data/synth_adv_ambiguous.json (모호 문항 id 목록). 지표에서는 빼거나 '되묻기' 문항으로 따로 본다.
"""
import json
import re
import sys
from collections import defaultdict

from common import HERE, OUT, read_jsonl

REC = re.compile(r"(HG\d{4}-\d+) / (\S+) /")
CARGO = re.compile(r"화물 (.+?) (\d+)상자")
IDQ = re.compile(r"(HG\d{4}-\d+|HL-\d{2}GP-\d+)")


def main():
    recs = defaultdict(dict)
    for p in read_jsonl(OUT / "adv" / "pages.jsonl"):
        lines = [x.strip() for x in p["text"].splitlines() if x.strip()]
        if len(lines) < 3 or "인계철" not in lines[0]:
            continue
        m = REC.match(lines[1])
        c = CARGO.search(" ".join(lines[2:4]))
        if m and c:
            recs[m.group(1)] = {"shipper": m.group(2), "cargo": c.group(1).strip(), "qty": c.group(2)}
    shippers = {r["shipper"] for r in recs.values()}
    cargos = {r["cargo"] for r in recs.values()}
    items = json.load(open(HERE / "data" / "synth_adv.json", encoding="utf-8"))
    amb = []
    for x in items:
        q = x["question"]
        if IDQ.search(q):
            continue
        qs = [s for s in shippers if s in q]
        if not qs:
            continue  # 인계철 기록 질문이 아님 (규칙·사본 문서 질문)
        qc = [c for c in cargos if c in q]
        qn = set(re.findall(r"(\d+)\s*상자", q))
        for s in qs:
            cand = [h for h, r in recs.items() if r["shipper"] == s
                    and (not qc or r["cargo"] in qc) and (not qn or r["qty"] in qn)]
            if len(cand) != 1:
                amb.append(x["id"])
                break
    (HERE / "data" / "synth_adv_ambiguous.json").write_text(json.dumps(amb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"예약 {len(recs)}건 | 합성 고급 {len(items)}문항 중 예약 특정 불가 {len(amb)}문항")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
