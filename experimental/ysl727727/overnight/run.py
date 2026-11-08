"""검색 파이프라인 변형 실행·채점 (API 비용 없음, 로컬 GPU).

    <gpu python> run.py <level,...> <preset,...>     예) run.py basic,inter,adv base,cand50

흐름: Dense(정확 코사인) + BM25 [+ 식별자 목록] → RRF 풀 → [리랭커] → [2-hop 식별자 확장 → 재리랭크]
      → [문서별 상한] → 최종 순위. 정답 파일은 모든 질문 검색이 끝난 뒤 채점에서만 읽는다.
결과: results/leaderboard.jsonl (한 줄 = 단계×설정), results/runs/<level>__<name>.json (질문별 순위)
"""
import json
from itertools import zip_longest
import pickle
import re
import statistics
import sys
import time

import numpy as np

from common import HERE, OUT, ROOT, SparseBM25, dedupe, gold, log, questions, read_jsonl, rrf, summarize, tokenize
from index import chunks, device, embed_queries, embeddings

RES = HERE / "results"
(RES / "runs").mkdir(parents=True, exist_ok=True)

IDRE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{0,6}(?:-[A-Z0-9]{1,8}){1,4}(?![A-Za-z0-9])")


def tokenize_id(text):
    """main 토큰화 + 하이픈 식별자 통째 토큰 (HG2029-00784 같은 예약·장비 번호를 한 덩어리로 맞춘다)."""
    return tokenize(text) + [m.lower() for m in IDRE.findall(text)]


_kiwi = None


def tokenize_kiwi(text):
    """Kiwi 형태소(명사·동사·형용사 어간, 외국어·숫자) + 하이픈 식별자 통째 토큰."""
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi

        _kiwi = Kiwi()
    keep = ("NN", "VV", "VA", "SL", "SN", "XR", "MAG")
    toks = [t.form.lower() for t in _kiwi.tokenize(text) if t.tag.startswith(keep)]
    return toks + [m.lower() for m in IDRE.findall(text)]


TOKENIZERS = {"main": lambda: tokenize, "id": lambda: tokenize_id, "kiwi": lambda: tokenize_kiwi}

BASE = {"chunks": "p800", "emb": "e5s", "tok": "main", "cand_d": 20, "cand_b": 20, "pool": 20,
        "rerank": "BAAI/bge-reranker-v2-m3", "entity": False, "hop2": 0, "cap": 0, "rrf_w": None,
        "qr": None, "graph": 0, "mmr": 0.0, "seed": 1, "pcap": 0, "mq": None, "route": 0, "ppr": 0, "gkey": False, "llmsel": 0,
        "gfix": False, "gfloor": 0.0, "selkeep": 0, "selil": False, "seleff": None, "selskip": 0}
# shortcut: 기록 키와 문서 번호를 걸린 청크 수(20개 초과면 문서 번호)로만 구분, 문서 양식이 바뀌면 메타데이터로 구분
KEY_MAX_CHUNKS = 20
PRESETS = {
    "naive": {"cand_b": 0, "rerank": None},
    "hybrid": {"rerank": None},
    "base": {},  # = main hybrid_rerank (Dense 20 + BM25 20 → RRF → 리랭커)
}


def preset(name):
    if name in PRESETS:
        return BASE | PRESETS[name]
    # 즉석 조합: base+cand50+cap2 같은 형식
    cfg = dict(BASE)
    for part in name.split("+"):
        if part in PRESETS:
            cfg |= PRESETS[part]
        elif part.startswith("cand"):
            n = int(part[4:])
            cfg |= {"cand_d": n, "cand_b": n, "pool": n}
        elif part.startswith("pool"):
            cfg["pool"] = int(part[4:])
        elif part.startswith("cap"):
            cfg["cap"] = int(part[3:])
        elif part == "idtok":
            cfg["tok"] = "id"
        elif part == "ent":
            cfg["entity"] = True
        elif part.startswith("hop"):
            cfg["hop2"] = int(part[3:])
        elif part.startswith("emb_"):
            cfg["emb"] = part[4:]
        elif part.startswith("ch_"):
            cfg["chunks"] = part[3:]
        elif part == "norr":
            cfg["rerank"] = None
        elif part.startswith("rr_"):
            cfg["rerank"] = RERANKERS[part[3:]]
        elif part.startswith("qr_"):
            cfg["qr"] = part[3:]
        elif part.startswith("graph"):
            cfg["graph"] = int(part[5:])
        elif part.startswith("mq_"):
            cfg["mq"] = part[3:]
        elif part == "gfix":
            cfg["gfix"] = True
        elif part.startswith("gfloor"):
            cfg["gfloor"] = float(part[6:])
        elif part.startswith("seff_"):  # LLM 집합 선택의 gpt-6-luna 추론 강도 (none/low/medium/high)
            cfg["seleff"] = part[5:]
        elif part.startswith("skip"):  # 질문 분해 결과가 N개 이하인 '단순 질문'은 LLM 집합 선택을 건너뜀
            cfg["selskip"] = int(part[4:])
        elif part.startswith("selkeep"):  # 앞 단계 상위 N개는 자리 고정, 그 뒤에 LLM 선택
            cfg["selkeep"] = int(part[7:])
        elif part == "selil":  # LLM 선택과 앞 단계 순서를 번갈아 배치
            cfg["selil"] = True
        elif part.startswith("sel"):
            cfg["llmsel"] = int(part[3:])
        elif part == "gkey":
            cfg["gkey"] = True
        elif part.startswith("ppr"):
            cfg["ppr"] = int(part[3:])
        elif part.startswith("route"):
            cfg["route"] = int(part[5:])
        elif part == "kiwi":
            cfg["tok"] = "kiwi"
        elif part.startswith("pcap"):
            cfg["pcap"] = int(part[4:])
        elif part.startswith("seed"):
            cfg["seed"] = int(part[4:])
        elif part.startswith("mmr"):
            cfg["mmr"] = float(part[3:])
        elif part == "base":
            pass
        else:
            raise ValueError(part)
    return cfg


RERANKERS = {"bge": "BAAI/bge-reranker-v2-m3", "mini": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
             "ko": "dragonkue/bge-reranker-v2-m3-ko"}


class Reranker:
    """cross-encoder(GPU면 fp16) + 점수 캐시 (같은 질문·청크는 다시 계산하지 않음)."""

    def __init__(self, level):
        self.level = level
        self.models = {}
        self.cache_path = OUT / level / "rerank_cache.pkl"
        try:
            self.cache = pickle.load(open(self.cache_path, "rb")) if self.cache_path.exists() else {}
        except Exception:  # noqa: BLE001  (다른 프로세스가 쓰는 중이면 빈 캐시로 시작)
            self.cache = {}
        self.dirty = 0
        self.seconds = 0.0

    def _model(self, mid):
        if mid not in self.models:
            import torch
            from sentence_transformers import CrossEncoder

            dev = device()
            kw = {"model_kwargs": {"torch_dtype": torch.float16}} if dev == "cuda" else {}
            m = CrossEncoder(mid, device=dev, **kw)
            m.max_length = 512
            self.models[mid] = m
        return self.models[mid]

    def score(self, mid, query, keys, texts):
        need = [i for i, k in enumerate(keys) if (mid, query, k) not in self.cache]
        if need:
            t = time.perf_counter()
            s = self._model(mid).predict([(query, texts[i]) for i in need], batch_size=32,
                                         show_progress_bar=False)
            self.seconds += time.perf_counter() - t
            for i, v in zip(need, s):
                self.cache[(mid, query, keys[i])] = float(v)
            self.dirty += len(need)
        return [self.cache[(mid, query, k)] for k in keys]

    def save(self):
        # shortcut: 파일 캐시라 여러 프로세스가 같은 순간에 저장하면 일부 항목이 빠질 수 있음, 팀 공용으로 쓰면 DB로
        if self.dirty:
            import os

            try:  # 다른 프로세스가 그사이 저장한 항목도 합쳐서 원자적으로 교체
                self.cache = pickle.load(open(self.cache_path, "rb")) | self.cache
            except Exception:  # noqa: BLE001
                pass
            tmp = self.cache_path.with_suffix(f".{os.getpid()}.tmp")
            with open(tmp, "wb") as f:
                pickle.dump(self.cache, f)
            os.replace(tmp, self.cache_path)
            self.dirty = 0


class Index:
    def __init__(self, level, variant, emb, tok):
        import torch

        self.rows = chunks(level, variant)
        self.variant = variant
        E = torch.tensor(embeddings(level, variant, emb), device=device())
        self.E = E if E.is_cuda else E.float()  # CPU는 fp16 행렬곱이 느려서 fp32로
        self.emb = emb
        self.bm25 = SparseBM25([r["index_text"] for r in self.rows], tokenizer=TOKENIZERS[tok]())
        self.ids = {}
        self.head_ids = []
        for i, r in enumerate(self.rows):
            for m in set(IDRE.findall(r["index_text"])):
                self.ids.setdefault(m, []).append(i)
            self.head_ids.append(set(IDRE.findall(r["heading"])))
        # 페이지 머리 5줄의 식별자 = 그 페이지가 다루는 기록(예약·장비·계약)의 키. 그래프의 노드로 쓴다.
        page_key = {}
        for p in read_jsonl(OUT / level / "pages.jsonl"):
            lines = [x.strip() for x in p["text"].splitlines() if x.strip()]
            page_key[(p["book_id"], p["page"])] = set(IDRE.findall(" ".join(lines[:5])))
        self.key_ids = [page_key.get((r["book_id"], r["page"]), set()) for r in self.rows]
        # 문서 카드(LLM 없음): 문서 머리 1~2줄 + 페이지 소제목(3줄째) 목록. 문서 라우팅에 쓴다.
        heads = {}
        for p in read_jsonl(OUT / level / "pages.jsonl"):
            lines = [x.strip() for x in p["text"].splitlines() if x.strip()]
            h = heads.setdefault(p["book_id"], {"top": " ".join(lines[:2]), "sec": {}})
            if len(lines) > 2:
                h["sec"].setdefault(lines[2][:60], None)
        self.books = sorted(heads)
        self.cards = [heads[b]["top"] + " | " + " / ".join(heads[b]["sec"]) for b in self.books]
        self.card_bm25 = SparseBM25(self.cards, tokenizer=tokenize)
        self.book_rows = {}
        for i, r in enumerate(self.rows):
            self.book_rows.setdefault(r["book_id"], []).append(i)
        self.key_index = {}
        for i, ks in enumerate(self.key_ids):
            for m in ks:
                self.key_index.setdefault(m, []).append(i)

    def dense(self, qv, n):
        import torch

        # shortcut: 전체 행렬곱 정확 검색, 청크가 수십만 개를 넘으면 ANN(Qdrant)으로
        s = self.E @ torch.tensor(qv, device=self.E.device, dtype=self.E.dtype)
        return torch.topk(s.float(), n).indices.tolist()


_index_cache = {}


def get_index(level, cfg):
    key = (level, cfg["chunks"], cfg["emb"], cfg["tok"])
    if key not in _index_cache:
        _index_cache[key] = Index(level, *key[1:])
    return _index_cache[key]


def entity_list(ix, q, limit=50):
    ids = set(IDRE.findall(q))
    if not ids:
        return []
    hits = {}
    for m in ids:
        for i in ix.ids.get(m, []):
            hits[i] = hits.get(i, 0) + 1
    bm = ix.bm25.scores(q)
    return sorted(hits, key=lambda i: (-hits[i], -bm[i], i))[:limit]


ENT_RE = re.compile(r"[가-힣]{2,3}(?:상사|무역|상공|기공|물산|산업|자재|부품|금속|기계|수출|기재|철재|상회)"
                    r"|\d{4}-\d{2}-\d{2}")


def chunk_entities(text):
    """LLM 없는 엔티티: 하이픈 식별자 + 회사명(접미사 규칙) + 날짜."""
    return set(IDRE.findall(text)) | set(ENT_RE.findall(text))


def build_graph(ix):
    """청크-엔티티 이분 그래프 (HippoRAG/LinearRAG의 LLM 없는 축소판). 열 정규화한 희소 행렬로 PPR을 돈다."""
    from scipy.sparse import csr_matrix

    ents, rows, cols = {}, [], []
    for i, r in enumerate(ix.rows):
        for e in chunk_entities(r["index_text"]):
            j = ents.setdefault(e, len(ents))
            rows.append(i)
            cols.append(j)
    n, m = len(ix.rows), len(ents)
    B = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, m))
    deg_c = np.asarray(B.sum(axis=1)).ravel() + 1e-9
    deg_e = np.asarray(B.sum(axis=0)).ravel() + 1e-9
    ix.g_ents, ix.g_B = ents, B
    ix.g_c2e = csr_matrix(B.multiply(1 / deg_c[:, None]))  # 청크 → 엔티티 (행 정규화)
    ix.g_e2c = csr_matrix(B.T.multiply(1 / deg_e[:, None]))  # 엔티티 → 청크


def ppr_chunks(ix, q, seeds, top, alpha=0.5, iters=15):
    """질문의 엔티티와 상위 seed 청크에서 출발하는 개인화 PageRank. 점수가 높은 청크 top개를 후보에 더한다."""
    if not hasattr(ix, "g_B"):
        build_graph(ix)
    qe = [ix.g_ents[e] for e in chunk_entities(q) if e in ix.g_ents]
    n, m = ix.g_B.shape
    p_c = np.zeros(n)
    p_e = np.zeros(m)
    if qe:
        p_e[qe] = 1.0 / len(qe)
    for i in seeds:
        p_c[i] += 1.0 / max(len(seeds), 1)
    tot = p_c.sum() + p_e.sum()
    p_c, p_e = p_c / tot, p_e / tot
    c, e = p_c.copy(), p_e.copy()
    for _ in range(iters):
        c_new = alpha * p_c + (1 - alpha) * (ix.g_e2c.T @ e)
        e_new = alpha * p_e + (1 - alpha) * (ix.g_c2e.T @ c)
        c, e = c_new, e_new
    order = np.argsort(-c)
    return [int(i) for i in order[: top + len(seeds)] if int(i) not in set(seeds)][:top]


def route_docs(ix, queries, m, pool):
    """문서 카드 BM25로 질의(하위 질의 포함)마다 상위 문서를 고르고, 후보에 없는 문서면
    그 문서의 청크 중 질의와 가장 가까운 2개를 리랭크 후보에 더한다 (하드 필터가 아니라 후보 보강)."""
    import torch

    have = {ix.rows[i]["book_id"] for i in pool}
    add = []
    for text, vec in queries:
        top = [ix.books[j] for j, _ in ix.card_bm25.search(text, m + len(have))]
        new = [b for b in top if b not in have][:m]
        for b in new:
            idx = ix.book_rows[b]
            sims = ix.E[idx] @ torch.tensor(vec, device=ix.E.device, dtype=ix.E.dtype)
            best = torch.topk(sims.float(), min(2, len(idx))).indices.tolist()
            add.extend(idx[j] for j in best)
    return add


def mmr_order(order, score, rows, lam):
    """같은 문서에서 이미 뽑은 수만큼 점수를 깎아 다시 고른다 (리랭커 점수 0~1 기준)."""
    left, out, per = list(order), [], {}
    while left:
        j = max(range(len(left)), key=lambda x: (score[left[x]] - lam * per.get(rows[left[x]]["book_id"], 0), -x))
        i = left.pop(j)
        out.append(i)
        per[rows[i]["book_id"]] = per.get(rows[i]["book_id"], 0) + 1
    return out


def graph_boost(ix, rr, cfg, q, order, score):
    """식별자 그래프 확장: 질문의 식별자(없으면 상위 seed개 청크 페이지의 기록 키)를 공유하는
    다른 문서의 청크를 문서당 1개씩 상위로 올린다. 리랭커가 같은 기록의 다른 문서(계약·사건·승인)를
    비슷한 다른 기록보다 낮게 매기는 문제를 겨냥한다."""
    qids = sorted(set(IDRE.findall(q)))  # 순서 고정 (set 순회 순서는 실행마다 달라짐)
    if qids:
        src = ix.key_index if cfg.get("gkey") else ix.ids  # gkey: 페이지 머리(기록 키)에 그 식별자가 있는 청크만
        linked = [i for m in qids for i in src.get(m, [])]
    else:
        seeds = sorted(set().union(*(ix.key_ids[i] for i in order[: cfg["seed"]])))
        if cfg.get("gfix"):
            # 문서 번호(HS-120처럼 한 문서 모든 쪽 머리에 찍힌 값)는 기록 키가 아니므로 제외
            seeds = [m for m in seeds if len(ix.key_index.get(m, [])) <= KEY_MAX_CHUNKS]
        linked = [i for m in seeds for i in ix.key_index.get(m, [])]
    linked = dedupe(linked)
    if not cfg.get("gfix"):
        linked = linked[:80]  # 1차 동작 (재현용)
    if not linked:
        return order
    new = [i for i in linked if i not in score]
    if new:
        s = rr.score(cfg["rerank"], q, [f"{ix.variant}:{ix.rows[i]['chunk_id']}" for i in new],
                     [ix.rows[i]["index_text"] for i in new])
        score.update(zip(new, s))
    best = {}
    for i in linked:
        b = ix.rows[i]["book_id"]
        if b not in best or score[i] > score[best[b]]:
            best[b] = i
    picks = sorted(best.values(), key=lambda i: (-score[i], ix.rows[i]["chunk_id"]))
    if cfg.get("gfloor"):
        picks = [i for i in picks if score[i] >= cfg["gfloor"]]  # 리랭커가 거의 무관하다고 본 청크는 올리지 않음
    picks = picks[: cfg["graph"]]
    head = order[:1] if qids else order[: cfg["seed"]]
    lifted = dedupe(head + picks)
    return lifted + [i for i in order if i not in set(lifted)]


def diversify(order, rows, cap):
    if not cap:
        return order
    per, first, rest = {}, [], []
    for i in order:
        b = rows[i]["book_id"]
        if per.get(b, 0) < cap:
            first.append(i)
            per[b] = per.get(b, 0) + 1
        else:
            rest.append(i)
    return first + rest


def retrieve(ix, rr, cfg, q, qv, out_n=20, subs=()):
    """subs: [(하위 질의 문장, 벡터)] — 다중 질의일 때만. q는 항상 원래 질문(식별자 그래프도 원래 질문 기준)."""
    queries = [(q, qv)] + list(subs)
    lists = []
    for text, vec in queries:
        lists.append(ix.dense(vec, cfg["cand_d"]))
        if cfg["cand_b"]:
            lists.append([i for i, _ in ix.bm25.search(text, cfg["cand_b"])])
    if cfg["entity"]:
        ent = entity_list(ix, q)
        if ent:
            lists.append(ent)
    pool_n = cfg["pool"] * len(queries) if cfg["rerank"] else max(cfg["pool"], out_n)
    pool = rrf(lists, pool_n, weights=cfg["rrf_w"])
    if cfg["route"]:
        pool = dedupe(pool + route_docs(ix, queries, cfg["route"], set(pool)))
    if cfg["ppr"]:
        pool = dedupe(pool + ppr_chunks(ix, q, pool[:5], cfg["ppr"]))
    if cfg["entity"]:
        pool = dedupe(pool + entity_list(ix, q)[:10])  # 질문의 식별자와 정확히 맞는 청크는 리랭크 후보에 꼭 넣는다
    if not cfg["rerank"]:
        return diversify(pool, ix.rows, cfg["cap"])[:out_n]
    keys = [f"{ix.variant}:{ix.rows[i]['chunk_id']}" for i in pool]
    texts = [ix.rows[i]["index_text"] for i in pool]
    per_query = [rr.score(cfg["rerank"], text, keys, texts) for text, _ in queries]
    s = [max(col) for col in zip(*per_query)]
    if len(queries) == 1:
        order = [pool[j] for j in sorted(range(len(pool)), key=lambda j: (-s[j], j))]
    else:
        orders = [[pool[j] for j in sorted(range(len(pool)), key=lambda j: (-sc[j], j))] for sc in per_query]
        order = rrf(orders, len(pool))
    score = dict(zip(pool, s))
    if cfg["hop2"]:
        # 2-hop: 상위 청크 머리말의 식별자(예약·장비 번호)가 나오는 다른 청크를 후보에 더해 다시 리랭크
        extra = []
        for i in order[: cfg["hop2"]]:
            for m in ix.head_ids[i]:
                extra.extend(ix.ids.get(m, [])[:30])
        extra = [i for i in dedupe(extra) if i not in set(pool)]
        if extra:
            s2 = rr.score(cfg["rerank"], q, [f"{ix.variant}:{ix.rows[i]['chunk_id']}" for i in extra],
                          [ix.rows[i]["index_text"] for i in extra])
            score.update(zip(extra, s2))
            order = sorted(score, key=lambda i: -score[i])
    if cfg["mmr"]:
        order = mmr_order(order, score, ix.rows, cfg["mmr"])
    if cfg["graph"]:
        order = graph_boost(ix, rr, cfg, q, order, score)
    if cfg["pcap"]:
        order = page_cap(order, ix.rows, cfg["pcap"])
    return diversify(order, ix.rows, cfg["cap"])[:out_n]


def page_cap(order, rows, cap):
    """같은 페이지 청크는 cap개까지만 앞에 두고 나머지는 뒤로 (같은 페이지 중복으로 칸을 쓰지 않게)."""
    per, first, rest = {}, [], []
    for i in order:
        key = (rows[i]["book_id"], rows[i]["page"])
        (first if per.get(key, 0) < cap else rest).append(i)
        per[key] = per.get(key, 0) + 1
    return first + rest


def llm_select_one(client, ix, cfg, question, order, k=8):
    """상위 cfg['llmsel']개 후보 중 LLM이 고른 최대 k개를 앞에, 나머지는 원래 순서로 뒤에 둔다."""
    import sel

    top = order[: cfg["llmsel"]]
    cands = [(ix.rows[i]["chunk_id"], ix.rows[i]["book_id"], ix.rows[i]["heading"], ix.rows[i]["text"]) for i in top]
    picked = [top[j] for j in sel.select(client, question, cands, k=k, effort=cfg.get("seleff"))]
    if cfg.get("selil"):
        mixed = [x for pair in zip_longest(picked, order) for x in pair if x is not None]
        return dedupe(mixed)
    if cfg.get("selkeep"):
        return dedupe(order[: cfg["selkeep"]] + picked + order)
    # shortcut: 고른 근거만 맨 앞에 두므로 정답 문서가 4개 이상이면 ④보다 낮아짐(REPORT_v2 10장), 다문서 질문이 많으면 고르는 개수를 늘리기
    chosen = set(picked)
    return picked + [i for i in order if i not in chosen]


def llm_select_all(ix, cfg, qs, orders, skip=frozenset()):
    from concurrent.futures import ThreadPoolExecutor

    from dotenv import load_dotenv
    from openai import OpenAI

    import sel

    load_dotenv(ROOT / ".env")
    client = OpenAI()

    def one(q):
        if q["id"] in skip:  # 단순 질문: LLM 집합 선택 없이 앞 단계 순서 그대로
            return orders[q["id"]]
        return llm_select_one(client, ix, cfg, q["question"], orders[q["id"]])

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, qs))
    sel.save()
    return {q["id"]: r for q, r in zip(qs, res)}


def query_text(level, cfg, q):
    if not cfg["qr"]:
        return q["question"]
    path = RES / f"qr_{cfg['qr']}_{level}.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    return m.get(q["id"]) or q["question"]


def evaluate(level, name, cfg=None, fp=False, save=True, syn=False, multi=False):
    """fp=True: 공식 development+additional에 거짓 전제 20문항을 더한다.
    syn=True: 합성 두 문서 세트(data/synth_<level>.json, 정상+거짓 전제)를 더한다. 이름 끝에 #fp / #syn / #all."""
    cfg = cfg or preset(name)
    extra, gold_extra = [], []
    if fp:
        extra.append(HERE / "data" / f"false_premise_{level}_questions.json")
        gold_extra.append(HERE / "data" / f"false_premise_{level}.json")
    if syn:
        extra.append(HERE / "data" / f"synth_{level}_questions.json")
        gold_extra.append(HERE / "data" / f"synth_{level}.json")
    if multi:  # 다문서 합성(정답 문서 3·4개): data/synth_multi_<level>.json
        extra.append(HERE / "data" / f"synth_multi_{level}_questions.json")
        gold_extra.append(HERE / "data" / f"synth_multi_{level}.json")
    ix = get_index(level, cfg)
    rr = Reranker(level) if cfg["rerank"] else None
    qs = questions(level, extra)
    texts = [query_text(level, cfg, q) for q in qs]
    t0 = time.perf_counter()
    qvs = embed_queries(texts, cfg["emb"])
    subs, n_decomp = {}, {}
    if cfg["mq"]:
        m = json.loads((RES / f"qr_{cfg['mq']}_{level}.json").read_text(encoding="utf-8"))
        for q in qs:
            v = m.get(q["id"]) or []
            v = [v] if isinstance(v, str) else v
            n_decomp[q["id"]] = len(v)
            subs[q["id"]] = [x for x in v if x.strip() and x.strip() != q["question"].strip()]
        flat = [x for q in qs for x in subs[q["id"]]]
        vecs = iter(embed_queries(flat, cfg["emb"])) if flat else iter([])
        subs = {qid: [(x, next(vecs)) for x in v] for qid, v in subs.items()}
    runs, ms, orders = {}, [], {}
    out_n = max(20, cfg["llmsel"])
    for q, text, qv in zip(qs, texts, qvs):
        t = time.perf_counter()
        orders[q["id"]] = retrieve(ix, rr, cfg, text, qv, out_n=out_n, subs=subs.get(q["id"], ()))
        ms.append((time.perf_counter() - t) * 1000)
    skip = frozenset(qid for qid, n in n_decomp.items() if n <= cfg["selskip"]) if cfg["selskip"] else frozenset()
    if cfg["llmsel"]:
        orders = llm_select_all(ix, cfg, qs, orders, skip)
        if skip:
            log(f"  단순 질문(분해 {cfg['selskip']}개 이하) {len(skip)}/{len(qs)}개는 집합 선택 생략")
    for q in qs:
        runs[q["id"]] = [(ix.rows[i]["book_id"], ix.rows[i]["page"], ix.rows[i]["chunk_id"])
                         for i in orders[q["id"]][:20]]
    if rr:
        rr.save()
    total = time.perf_counter() - t0
    # ---- 여기서부터 채점 (정답 파일 읽기) ----
    g = gold(level, gold_extra)
    summ, per_q = summarize({k: [(b, p) for b, p, _ in v] for k, v in runs.items()}, g)
    if fp or syn or multi:
        name = name + ("#multi" if multi else "#all" if fp and syn else "#fp" if fp else "#syn")
        groups = {"fp": lambda s: s.startswith("false_premise"),
                  "syn": lambda s: s.startswith("synth") and not s.startswith("synth_multi"),
                  "multi": lambda s: s.startswith("synth_multi"),
                  "official": lambda s: s in ("development", "additional")}
        split_of = {q["id"]: q["split"] for q in qs}
        for key, test in groups.items():
            sel_runs = {k: [(b, p) for b, p, _ in v] for k, v in runs.items() if test(split_of[k])}
            if sel_runs:
                summ = summ | {key: summarize(sel_runs, g)[0]}
    row = {"level": level, "name": name, **summ, "ms_median": statistics.median(ms),
           "total_s": total, "cfg": cfg, "time": time.strftime("%H:%M:%S")}
    if save:
        with open(RES / "leaderboard.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        (RES / "runs" / f"{level}__{name}.json").write_text(
            json.dumps({"cfg": cfg, "runs": runs, "per_q": per_q}, ensure_ascii=False), encoding="utf-8")
    log(f"{level:5s} {name:28s} R@5 {summ['recall@5']:.3f} R@8 {summ['recall@8']:.3f} R@10 {summ['recall@10']:.3f} "
        f"| H@8 {summ['hit@8']:.3f} pg@8 {summ['page@8']:.3f} R@20 {summ['recall@all']:.3f} | n={summ['n']} "
        f"{statistics.median(ms):.0f}ms")
    for key, label in (("official", "  └ 공식만"), ("fp", "  └ 거짓전제만"), ("syn", "  └ 합성만"),
                       ("multi", "  └ 다문서 합성만")):
        if (fp or syn or multi) and key in summ:
            f = summ[key]
            log(f"{'':5s} {label:28s} R@5 {f['recall@5']:.3f} R@8 {f['recall@8']:.3f} R@10 {f['recall@10']:.3f} "
                f"| H@8 {f['hit@8']:.3f} pg@8 {f['page@8']:.3f} R@20 {f['recall@all']:.3f} | n={f['n']}")
    return row


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import warnings

    warnings.filterwarnings("ignore")
    levels = sys.argv[1].split(",")
    names = sys.argv[2].split(",")
    mode = sys.argv[3] if len(sys.argv) > 3 else ""
    for lv in levels:
        for nm in names:
            evaluate(lv, nm, fp=mode in ("fp", "all"), syn=mode in ("syn", "all"), multi=mode == "multi")
