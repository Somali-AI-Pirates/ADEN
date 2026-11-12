"""Preserve matched cells/paragraphs through one reranking per request."""

import re
from time import perf_counter

import tiktoken

from experimental.u_10o0o.bm25 import tokenize_bigram_id_normalized
from experimental.u_10o0o.hybrid import weighted_rrf
from experimental.u_10o0o.table_rag.retrieval import (
    distinct_page_units,
    merge_pages,
    page_key,
    source_id,
)


def normalized(value):
    return re.sub(r"\s+", "", value)


def raw_card(page, query):
    text = page["text"]
    terms = set(tokenize_bigram_id_normalized(query))
    # Original paragraphs and neighboring lines rather than an unconditional page prefix.
    spans = list(re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+){0,3}", text))
    if not spans:
        spans = [re.match(r"[\s\S]*", text)]
    best = max(
        spans, key=lambda m: len(terms & set(tokenize_bigram_id_normalized(m.group())))
    )
    return {
        "id": source_id(page) + f":raw:{best.start()}:{best.end()}",
        "source": source_id(page),
        "heading": "",
        "section": "",
        "kind": "original_excerpt",
        "table_id": None,
        "line_start": text[: best.start()].count("\n") + 1,
        "line_end": text[: best.end()].count("\n") + 1,
        "text": best.group(),
        "fields": "{}",
        "scope_id": source_id(page),
    }


def cards_for(base, catalog, page, query, table_id=None):
    cards = catalog.page_cards(source_id(page), query, table_id=table_id, limit=5)
    return cards or [raw_card(page, query)]


def search(base, catalog, query, *, policies=False):
    started = perf_counter()
    with base.embedding_lock:
        base.calls += 1
        vector = base._encode_query(query)
    dense = base._dense_rank(vector)
    sparse = base.bm25.fielded(query, top_k=50)
    candidates = distinct_page_units(
        weighted_rrf(sparse, dense, (0.5, 0.5), top_k=100), 20
    )
    related = catalog.discover(query, 4) if policies else []
    if related:
        # B adds a bounded source-section channel, preserving the original hybrid list and total candidate count.
        relation = [(base.by_source[c["source"]], 0.0) for c in related]
        candidates = merge_pages([candidates, relation], 20)
    chosen = []
    for unit, score in candidates:
        cards = cards_for(base, catalog, unit, query, unit.get("table_id"))
        rendered = "\n\n".join(
            dict.fromkeys(
                c["heading"] + "\n" + c["section"] + "\n" + c["text"] for c in cards
            )
        )
        chosen.append((unit, score, cards, rendered))
    with base.reranking_lock:
        ids = base.reranker.tokenizer(
            [query] * len(chosen),
            [r[3] for r in chosen],
            truncation=False,
            verbose=False,
        )["input_ids"]
        scores = base.reranker.predict(
            [(query, r[3]) for r in chosen],
            batch_size=4,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
    ordered = sorted(
        zip(chosen, scores, strict=True),
        key=lambda r: (-float(r[1]), source_id(r[0][0])),
    )
    ranked = [(base.page_map[page_key(v[0])], float(score)) for v, score in ordered]
    units = [
        {"source_id": source_id(v[0]), "table_id": v[0].get("table_id")}
        for v, _ in ordered
    ]
    card_map = {source_id(v[0]): v[2] for v, _ in ordered}
    tables = list(
        dict.fromkeys(
            t for p, _ in ranked for t in base.store.by_page.get(page_key(p), [])
        )
    )[:6]
    return {
        "query": query,
        "kind": "documents",
        "ranked": ranked,
        "tables": tables,
        "unit_candidates": units,
        "evidence_cards": card_map,
        "related_policy_sources": [c["source"] for c in related],
        "work": {
            "rerank_pairs": len(chosen),
            "raw_pair_tokens": sum(map(len, ids)),
            "truncated_pairs": sum(len(x) > base.reranker.max_length for x in ids),
        },
        "seconds": perf_counter() - started,
    }


def evidence_view(base, catalog, searches, requests):
    enc = tiktoken.get_encoding("o200k_base")
    groups = [s["ranked"] for s in searches]
    ranked = merge_pages(groups, 20)
    allowed = {source_id(p) for p, _ in ranked}
    registry, outputs, table_ids = {}, [], []
    for page, _ in ranked:
        sid = source_id(page)
        hits = [s for s in searches if sid in {source_id(p) for p, _ in s["ranked"]}]
        bindings = list(
            dict.fromkeys(rid for s in hits for rid in s.get("request_ids", []))
        )
        candidates = []
        for s in hits:
            candidates.extend(
                s.get("evidence_cards", {}).get(sid, [])
                or cards_for(base, catalog, page, s["query"])
            )
        selected, used = [], 0
        for c in candidates:
            if c["id"] in registry:
                continue
            tokens = enc.encode(c["text"])
            if selected and used + min(len(tokens), 500) > 650:
                continue
            display = enc.decode(tokens[:500])
            registry[c["id"]] = c
            selected.append(
                {
                    "evidence_id": c["id"],
                    "source_id": sid,
                    "heading": c["heading"],
                    "section": c["section"],
                    "original_lines": [c["line_start"], c["line_end"]],
                    "text": display,
                    "truncated": len(tokens) > 500,
                    "relation": "same original page and section; applicability must be checked",
                }
            )
            used += min(len(tokens), 500)
            if len(selected) >= 5:
                break
        outputs.append(
            {"source_id": sid, "request_ids": bindings, "evidence": selected}
        )
        for tid in base.store.by_page.get(page_key(page), []):
            if tid not in table_ids and len(table_ids) < 6:
                table_ids.append(tid)
    return (
        {
            "requests": requests,
            "candidate_pages": outputs,
            "tables": base.store.schema(table_ids),
            "candidate_count": len(allowed),
            "source_budget": 8,
        },
        registry,
        ranked,
    )
