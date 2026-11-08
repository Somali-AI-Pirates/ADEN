"""LLM 집합 선택: 검색 상위 후보 N개 중 답에 필요한 근거를 함께 덮는 최대 k개를 고른다 (질문당 1회 호출).

리랭커는 청크를 하나씩 따로 점수 매기므로 "규칙 문서 + 해당 기록 + 정정·승인"처럼 서로 다른 정보를
함께 갖춰야 하는 질문에서 비슷한 기록만 위로 올리기 쉽다. 그래서 마지막에 LLM이 후보 묶음을 한 번에 보고
빠진 종류가 없도록 고르게 한다. 정답 파일은 쓰지 않는다. 결과는 캐시(results/llmsel_cache.json)에 저장한다.
"""
import json
import threading

from common import HERE

MODEL = "gpt-6-luna"
CACHE = HERE / "results" / "llmsel_cache.json"
_lock = threading.Lock()
_cache = None
SYSTEM = ("너는 업무 문서 검색 결과에서 답변 근거를 고르는 선택기다. 질문에 정확히 답하는 데 필요한 근거를 "
          "후보 중에서 최대 {k}개 골라라. 규칙: (1) 질문의 판단·계산에 필요한 서로 다른 정보(적용 규칙·기준·요율·계약 조건, "
          "해당 예약·장비의 실제 기록, 정정·승인·예외)를 빠짐없이 덮도록 고른다. (2) 같은 내용의 중복 후보는 하나만 고른다. "
          "(3) 질문 대상과 다른 예약·장비의 기록은 고르지 않는다. (4) 중요한 것부터 나열한다. "
          "출력은 후보 번호의 JSON 정수 배열 하나만.")


def _load():
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    return _cache


def save():
    """다른 프로세스가 그사이 저장한 항목과 합친 뒤 임시 파일로 써서 교체한다(덮어쓰기로 항목이 사라지지 않게)."""
    import os

    with _lock:
        if _cache is None:
            return
        try:
            disk = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        except Exception:  # noqa: BLE001
            disk = {}
        merged = disk | _cache
        tmp = CACHE.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CACHE)


def select(client, question, cands, k=8, snippet=260, effort=None):
    """cands: [(chunk_id, book_id, heading, text)] 순위 순. 반환: 고른 후보의 인덱스 목록(최대 k).
    effort: gpt-6-luna 추론 강도(none/low/medium/high). None이면 모델 기본값(기존 결과와 같은 캐시 키)."""
    key = question + "||" + "|".join(c[0] for c in cands) + f"||{k}" + (f"||{effort}" if effort else "")
    extra = {"reasoning_effort": effort} if effort else {}
    cache = _load()
    with _lock:
        if key in cache:
            return cache[key]
    # shortcut: 후보마다 앞 260자만 보여 줌, 긴 표 뒤쪽 근거를 자주 놓치면 snippet을 늘리기
    lines = [f"[{i}] ({c[1]}) {c[2]}\n{c[3][:snippet]}" for i, c in enumerate(cands)]
    user = f"질문: {question}\n\n후보:\n" + "\n\n".join(lines)
    picked, ok = [], False
    for _ in range(3):
        try:
            r = client.chat.completions.create(
                model=MODEL, max_completion_tokens=3000,
                messages=[{"role": "system", "content": SYSTEM.replace("{k}", str(k))},
                          {"role": "user", "content": user}], **extra)
            with _lock, open(HERE / "results" / "cost_ledger.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"tag": "llmsel" + (f"_{effort}" if effort else ""), "in": r.usage.prompt_tokens,
                                    "out": r.usage.completion_tokens}) + "\n")
            raw = r.choices[0].message.content
            arr = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
            picked = [int(x) for x in arr if isinstance(x, (int, float)) and 0 <= int(x) < len(cands)]
            ok = True
            break
        except Exception:  # noqa: BLE001
            continue
    picked = list(dict.fromkeys(picked))[:k]
    if ok:  # 호출 실패(빈 응답·형식 오류)는 캐시에 남기지 않는다. 다음 실행에서 다시 시도
        with _lock:
            cache[key] = picked
    return picked
