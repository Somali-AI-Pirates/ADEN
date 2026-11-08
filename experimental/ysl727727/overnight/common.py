"""밤샘 검색 실험 공통 코드 (개인 실험, 로컬 전용, main 코드는 import만 한다).

규칙
- 검색·색인은 PDF와 질문 문장만 쓴다. 정답 JSON(evidence_groups 등)은 검색이 끝난 뒤 채점에만 읽는다.
- 벡터 검색은 로컬(GPU 정확 검색 또는 FAISS)에서만 한다. 팀 Qdrant Cloud는 쓰지 않는다.
"""
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts" / "overnight"
RAW = Path(r"C:\Users\ysl72\Desktop\123")  # shortcut: 원본 데이터 경로를 제 PC로 고정, 다른 PC에서는 이 줄만 바꾸기

LEVELS = {
    "basic": {"docs": [ROOT / "data/basic/documents"], "eval": ROOT / "data/basic/evaluation",
              "splits": ["development", "additional"]},
    "inter": {"docs": sorted(RAW.glob("inter/Intermediate_shipping_part*/documents")),
              "eval": RAW / "inter/Intermediate_shipping_part01of04/evaluation",
              "splits": ["development", "additional"]},
    "adv": {"docs": sorted(RAW.glob("advanced/Advanced_shipping_part*/documents")),
            "eval": RAW / "advanced/Advanced_shipping_part01of09/evaluation",
            "splits": ["development", "additional"]},
}


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def questions(level, extra=()):
    """검색 입력: id·question만 (정답 없음)."""
    lv = LEVELS[level]
    qs = []
    for s in lv["splits"]:
        for q in json.load(open(lv["eval"] / f"{s}_questions.json", encoding="utf-8")):
            qs.append({"id": q["id"], "question": q["question"], "split": s})
    for path in extra:
        for q in json.load(open(path, encoding="utf-8")):
            qs.append({"id": q["id"], "question": q["question"], "split": Path(path).stem})
    return qs


def gold(level, extra=()):
    """채점 전용. 검색이 끝난 뒤에만 부른다."""
    lv = LEVELS[level]
    out = {}
    for s in lv["splits"]:
        for q in json.load(open(lv["eval"] / f"{s}.json", encoding="utf-8")):
            out[q["id"]] = q
    for path in extra:
        for q in json.load(open(path, encoding="utf-8")):
            out[q["id"]] = q
    return out


# ---------- 채점 ----------
def score_one(top, g, ks=(5, 8, 10)):
    """top: [(book_id, page)] 순위 목록. 공식 지표(문서 Recall/Hit) + 보조(근거쪽 = 그룹별 페이지 포함)."""
    groups = [grp for grp in g["evidence_groups"] if grp]
    books = {e["book_id"] for grp in groups for e in grp}
    if not books:
        return None
    r = {}
    for k in ks:
        t = top[:k]
        found = {b for b, _ in t} & books
        pages = set(t)
        r[f"recall@{k}"] = len(found) / len(books)
        r[f"hit@{k}"] = float(bool(found))
        r[f"page@{k}"] = sum(any((e["book_id"], e["page"]) in pages for e in grp) for grp in groups) / len(groups)
    r["recall@all"] = len({b for b, _ in top} & books) / len(books)
    r["n_books"] = len(books)
    return r


def summarize(runs, golds, ks=(5, 8, 10)):
    """runs: {qid: [(book, page), ...]} → 평균 지표."""
    rows = {}
    for qid, top in runs.items():
        s = score_one(top, golds[qid], ks)
        if s is not None:
            rows[qid] = s
    keys = [f"{m}@{k}" for k in ks for m in ("recall", "hit", "page")] + ["recall@all"]
    return {key: float(np.mean([r[key] for r in rows.values()])) for key in keys} | {"n": len(rows)}, rows


# ---------- BM25 (main의 KeywordIndex와 같은 토큰화·공식, 희소행렬로 빠르게) ----------
TOKEN = re.compile(r"[가-힣]+|[a-z0-9]+")


def tokenize(text):
    tokens = []
    for part in TOKEN.findall(text.lower()):
        if part.isascii() or len(part) == 1:
            tokens.append(part)
        else:
            tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
    return tokens


class SparseBM25:
    def __init__(self, texts, k1=1.5, b=0.75, tokenizer=tokenize):
        from collections import Counter

        from scipy.sparse import csc_matrix

        self.tokenizer = tokenizer
        vocab, rows, cols, vals = {}, [], [], []
        lens = []
        for i, t in enumerate(texts):
            tf = Counter(tokenizer(t))
            lens.append(sum(tf.values()))
            for term, c in tf.items():
                j = vocab.setdefault(term, len(vocab))
                rows.append(i)
                cols.append(j)
                vals.append(c)
        n = len(texts)
        lens = np.array(lens, dtype=np.float64)
        avg = lens.mean()
        rows, cols, vals = np.array(rows), np.array(cols), np.array(vals, dtype=np.float64)
        df = np.bincount(cols, minlength=len(vocab))
        idf = np.log(1 + (n - df + 0.5) / (df + 0.5))
        norm = k1 * (1 - b + b * lens[rows] / avg)
        w = idf[cols] * vals * (k1 + 1) / (vals + norm)
        self.W = csc_matrix((w, (rows, cols)), shape=(n, len(vocab)))
        self.vocab = vocab
        self.n = n

    def scores(self, query):
        terms = {self.vocab[t] for t in self.tokenizer(query) if t in self.vocab}
        if not terms:
            return np.zeros(self.n)
        return np.asarray(self.W[:, sorted(terms)].sum(axis=1)).ravel()

    def search(self, query, top_n):
        s = self.scores(query)
        nz = np.flatnonzero(s > 0)
        order = nz[np.lexsort((nz, -s[nz]))][:top_n]
        return [(int(i), float(s[i])) for i in order]


def rrf(lists, top_n, k=60, weights=None):
    """lists: 각 순위 목록(정수 id). 동점은 id 순."""
    scores = {}
    for li, ids in enumerate(lists):
        w = 1.0 if weights is None else weights[li]
        for rank, i in enumerate(ids, start=1):
            scores[i] = scores.get(i, 0.0) + w / (k + rank)
    return sorted(scores, key=lambda i: (-scores[i], i))[:top_n]


def dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def log(*a):
    print(*a, flush=True)


def fmt(x):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.3f}"
