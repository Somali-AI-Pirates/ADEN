"""gpt-6-luna가 추론 강도(reasoning_effort) 설정을 받는지, 값마다 추론 토큰·시간이 어떻게 달라지는지 확인.

    <gpu python> effort_probe.py
질문 분해 프롬프트로 같은 질문 하나를 값마다 3번씩 호출한다. 비용은 results/cost_ledger.jsonl에 기록.
"""
import json
import sys
import time

from dotenv import load_dotenv

import qr
from common import HERE, ROOT, questions

EFFORTS = [None, "none", "minimal", "low", "medium", "high"]


def main():
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    client = OpenAI()
    q = questions("inter")[3]["question"]
    for eff in EFFORTS:
        extra = {} if eff is None else {"reasoning_effort": eff}
        rows = []
        for _ in range(3):
            t = time.perf_counter()
            try:
                r = client.chat.completions.create(
                    model=qr.MODEL, max_completion_tokens=2000,
                    messages=[{"role": "system", "content": qr.DECOMP}, {"role": "user", "content": q}], **extra)
            except Exception as e:  # noqa: BLE001
                print(f"{str(eff):8s} 오류: {type(e).__name__}: {str(e)[:160]}")
                break
            ms = (time.perf_counter() - t) * 1000
            u = r.usage
            det = getattr(u, "completion_tokens_details", None)
            reas = getattr(det, "reasoning_tokens", None) if det else None
            with open(HERE / "results" / "cost_ledger.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"tag": "probe", "in": u.prompt_tokens, "out": u.completion_tokens}) + "\n")
            rows.append((ms, u.completion_tokens, reas, r.choices[0].message.content.strip()[:80]))
        if rows:
            print(f"{str(eff):8s} 시간 {[round(x[0] / 1000, 2) for x in rows]}초 | 출력 토큰 {[x[1] for x in rows]} "
                  f"| 추론 토큰 {[x[2] for x in rows]}")
            print(f"         예: {rows[0][3]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
