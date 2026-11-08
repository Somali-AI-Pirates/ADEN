"""핵심 로직 자체 검사 (모델·API·데이터 없이 몇 초). 실패하면 AssertionError.

    python experimental/ysl727727/overnight/selfcheck.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qr  # noqa: E402
import run  # noqa: E402
import sel  # noqa: E402
from common import dedupe, rrf, score_one  # noqa: E402
from index import device  # noqa: E402

# 설정 이름 해석: 최종 후보(v2 + 추론 none + 건너뛰기)
c = run.preset("base+graph6+gfix+gfloor0.05+mmr0.2+mq_decomp_none+sel30+seff_none+skip1")
assert (c["graph"], c["gfix"], c["gfloor"], c["mmr"]) == (6, True, 0.05, 0.2), c
assert (c["mq"], c["llmsel"], c["seleff"], c["selskip"]) == ("decomp_none", 30, "none", 1), c
assert run.preset("base")["rerank"] == "BAAI/bge-reranker-v2-m3"
try:
    run.preset("base+오타")
    raise AssertionError("모르는 설정 이름을 받아들임")
except ValueError:
    pass

# 질문 분해 종류 꼬리: 추론 강도 / 로컬 모델 / 기본
assert qr.split_kind("decomp_none") == ("decomp", "none", None)
assert qr.split_kind("decomp_qwen4b") == ("decomp", None, qr.LOCAL["qwen4b"])
assert qr.split_kind("decomp") == ("decomp", None, None)

# ② MMR: 같은 문서 두 번째 조각은 0.2 감점돼 다른 문서 뒤로 밀린다
rows = [{"book_id": "A"}, {"book_id": "A"}, {"book_id": "B"}]
assert run.mmr_order([0, 1, 2], {0: 0.9, 1: 0.8, 2: 0.7}, rows, 0.2) == [0, 2, 1]
assert run.mmr_order([0, 1, 2], {0: 0.9, 1: 0.8, 2: 0.7}, rows, 0.0) == [0, 1, 2]

# 지표: 문서 Recall과 정답 페이지(근거 그룹마다 한 페이지라도 들어오면 맞음)
g = {"evidence_groups": [[{"book_id": "A", "page": 3}], [{"book_id": "B", "page": 5}, {"book_id": "B", "page": 6}]]}
s = score_one([("A", 9), ("B", 6), ("C", 1)], g, ks=(1, 8))
assert (s["recall@1"], s["recall@8"], s["page@8"], s["hit@1"]) == (0.5, 1.0, 0.5, 1.0), s
assert score_one([("A", 3)], {"evidence_groups": []}) is None  # 근거 없는 질문은 집계에서 뺀다

# RRF·중복 제거
assert rrf([[1, 2, 3], [3, 1]], top_n=3) == [1, 3, 2]
assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]

# ⑤ 집합 선택: LLM 응답에서 범위 밖·중복·숫자 아닌 값을 버리고, 추론 강도를 그대로 넘긴다
seen = {}


def fake_create(**kw):
    seen.update(kw)
    msg = SimpleNamespace(content='고른 근거: [3, 1, 1, 99, "x"]')
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))


with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "results").mkdir()
    sel.HERE, sel._cache = Path(tmp), {}  # 실제 캐시·비용 장부는 건드리지 않음
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    cands = [(f"c{i}", "B1", "제목", "본문") for i in range(5)]
    assert sel.select(client, "질문", cands, k=8, effort="none") == [3, 1]
    assert seen.get("reasoning_effort") == "none"

# 장치 선택: OV_DEVICE가 있으면 그대로 따른다
os.environ["OV_DEVICE"] = "cpu"
assert device() == "cpu"

print("selfcheck 통과")
