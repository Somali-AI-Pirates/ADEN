"""Question requirements and source-checked intermediate evidence.

Semantic support is a model judgment. Literal quotes/cells, source identity,
retention and the final token/page budgets are checked in Python.
"""

import json
import re
from collections import Counter

import tiktoken

from experimental.u_10o0o.context_evidence import _count_tokens
from experimental.u_10o0o.table_rag.retrieval import (
    json_payload,
    merge_pages,
    source_id,
)


def flat(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def requirements_from(question, rows):
    if not 1 <= len(rows) <= 8:
        raise ValueError("Expected one to eight question requirements")
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate requirement IDs")
    if any(
        not flat(r["question_quote"]) or flat(r["question_quote"]) not in flat(question)
        for r in rows
    ):
        raise ValueError("Requirement is not anchored in the original question")
    return rows


def observations(result, sql_result, step):
    """Only text/cells actually shown in this step become citable evidence."""
    entries = {}
    for page in result["passages"]:
        sid = page["source_id"]
        entries[sid] = {
            "id": sid,
            "kind": "document",
            "text": page["text"],
            "source_ids": [sid],
        }
    if sql_result and sql_result["status"] == "ok":
        sources = sorted(
            {f"{p['book_id']}:p{p['page']:04d}" for p in sql_result["sources"]}
        )
        for row_number, row in enumerate(sql_result["rows"]):
            for column, value in row.items():
                eid = f"sql{step}:{row_number}:{column}"
                text = (
                    value
                    if isinstance(value, str)
                    else json.dumps(value, ensure_ascii=False)
                )
                entries[eid] = {
                    "id": eid,
                    "kind": "sql",
                    "text": text,
                    "source_ids": sources,
                    "query_id": f"sql{step}",
                    "row": row_number,
                    "column": column,
                    "value": value,
                    "preview_truncated": sql_result["preview_truncated"],
                }
    return entries


class Ledger:
    def __init__(self, requirements):
        self.requirements = requirements
        self.states = {
            r["id"]: {"status": "missing", "detail": "Not checked"}
            for r in requirements
        }
        self.claims = []
        self.proofs = {}
        self.sql = {}
        self.invalid = []

    def ingest(self, answer, visible, sql=None, step=1):
        if sql is not None:
            self.sql[f"sql{step}"] = sql
        for assessment in answer["assessments"]:
            rid = assessment["requirement_id"]
            if rid not in self.states:
                self.invalid.append({"step": step, "reason": "unknown_requirement"})
                continue
            accepted = []
            for claim in assessment["claims"]:
                supports = []
                invalid_claim = False
                for citation in claim["citations"]:
                    proof = visible.get(citation["evidence_id"])
                    quote = flat(citation["quote"])
                    valid = (
                        proof
                        and quote
                        and (
                            quote == flat(proof["text"])
                            if proof["kind"] == "sql"
                            else quote in flat(proof["text"])
                        )
                    )
                    if not valid:
                        invalid_claim = True
                        self.invalid.append(
                            {
                                "step": step,
                                "requirement_id": rid,
                                "reason": "unobserved_or_inexact_citation",
                            }
                        )
                        continue
                    # Persist only the cited passage/cell, not the whole page.
                    key = f"e{len(self.proofs) + 1}"
                    self.proofs[key] = {
                        **proof,
                        "quote": citation["quote"],
                        "text": citation["quote"],
                    }
                    supports.append(key)
                if supports and not invalid_claim:
                    accepted.append(
                        {
                            "id": f"a{len(self.claims) + len(accepted) + 1}",
                            "requirement_id": rid,
                            "statement": claim["statement"],
                            "supports": supports,
                            "step": step,
                        }
                    )
            self.claims.extend(accepted)
            status = assessment["status"]
            if status == "supported" and not accepted:
                status = "missing"
                self.invalid.append(
                    {
                        "step": step,
                        "requirement_id": rid,
                        "reason": "unsupported_completion",
                    }
                )
            self.states[rid] = {
                "status": status,
                "detail": assessment["missing_detail"],
            }

    def coverage(self, kept=None):
        retained = {c["id"] for c in (self.claims if kept is None else kept)}
        rows = []
        for req in self.requirements:
            rid = req["id"]
            expected = {c["id"] for c in self.claims if c["requirement_id"] == rid}
            status = self.states[rid]["status"]
            lost = bool(expected - retained)
            if status == "supported" and (lost or not expected):
                status = "partial" if expected & retained else "missing"
            rows.append(
                {
                    **req,
                    "status": status,
                    "budget_lost_evidence": lost,
                    "detail": self.states[rid]["detail"],
                }
            )
        return rows

    def complete(self):
        return bool(self.requirements) and all(
            r["status"] == "supported" for r in self.coverage()
        )

    def payload(self, kept, pages=()):
        needed = {p for c in kept for p in c["supports"]}
        proofs = {p: self.proofs[p] for p in sorted(needed)}
        sql_ids = {p["query_id"] for p in proofs.values() if p["kind"] == "sql"}
        return {
            "pages": list(pages),
            "requirements": self.coverage(kept),
            "intermediate_answers": kept,
            "supporting_evidence": proofs,
            "sql_operations": [
                {
                    "id": q,
                    "sql": self.sql[q]["sql"],
                    "status": self.sql[q]["status"],
                    "tables": self.sql[q].get("tables", []),
                    "source_scope": self.sql[q].get("source_scope"),
                    "preview_truncated": self.sql[q].get("preview_truncated", False),
                }
                for q in sorted(sql_ids)
            ],
            "coverage_is_model_judgment": True,
        }

    def public_state(self):
        return {"coverage": self.coverage(), "intermediate": self.payload(self.claims)}

    def pack(self, groups, page_map, *, token_limit=8000, page_limit=8):
        # Fair first pass across requested records, then additional claims.
        queues = [
            [c for c in self.claims if c["requirement_id"] == r["id"]]
            for r in self.requirements
        ]
        ordered = [
            queue[i]
            for i in range(max(map(len, queues), default=0))
            for queue in queues
            if i < len(queue)
        ]
        kept = []
        for claim in ordered:
            if _count_tokens(json_payload(self.payload([*kept, claim]))) <= token_limit:
                kept.append(claim)
        if _count_tokens(json_payload(self.payload(kept))) > token_limit:
            raise ValueError("Requirements alone exceed the evidence budget")
        preferred = list(
            dict.fromkeys(
                sid
                for c in kept
                for key in c["supports"]
                for sid in self.proofs[key]["source_ids"]
            )
        )
        original = merge_pages(groups, limit=200)
        lookup = {source_id(p): (p, score) for p, score in original}
        for sid in preferred:
            if sid not in lookup and sid in page_map:
                lookup[sid] = (page_map[sid], 0.0)
        ranked = [lookup[s] for s in preferred if s in lookup]
        ranked += [pair for pair in original if source_id(pair[0]) not in preferred]
        ranked = ranked[:20]
        pages = []
        enc = tiktoken.get_encoding("o200k_base")
        for page, _ in ranked:
            if len(pages) >= page_limit:
                break
            item = {
                "book_id": page.get("book_id")
                or page["file_name"].removesuffix(".pdf"),
                "page": page["page"],
                "source_id": source_id(page),
                "text": page["text"],
            }
            candidate = self.payload(kept, [*pages, item])
            if _count_tokens(json_payload(candidate)) <= token_limit:
                pages.append(item)
                continue
            tokens = enc.encode(item["text"])
            lo, hi = 0, len(tokens)
            while lo < hi:
                middle = (lo + hi + 1) // 2
                truncated = {
                    **item,
                    "text": enc.decode(tokens[:middle]),
                    "truncated": True,
                }
                if (
                    _count_tokens(json_payload(self.payload(kept, [*pages, truncated])))
                    <= token_limit
                ):
                    lo = middle
                else:
                    hi = middle - 1
            if lo >= 80:
                pages.append(
                    {**item, "text": enc.decode(tokens[:lo]), "truncated": True}
                )
        payload = self.payload(kept, pages)
        tokens = _count_tokens(json_payload(payload))
        assert tokens <= token_limit and len(pages) <= page_limit
        proof_sources = sorted(
            {
                sid
                for p in payload["supporting_evidence"].values()
                for sid in p["source_ids"]
            }
        )
        return {
            "payload": payload,
            "ranked": ranked,
            "tokens": tokens,
            "raw_pages": len(pages),
            "provenance_sources": proof_sources,
            "coverage_counts": dict(
                Counter(r["status"] for r in payload["requirements"])
            ),
            "complete": bool(payload["requirements"])
            and all(r["status"] == "supported" for r in payload["requirements"]),
            "dropped_claims": len(self.claims) - len(kept),
        }
