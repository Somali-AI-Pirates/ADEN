"""Replay the archived teammate preset on the 6,500-item Advanced bench."""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import torch
from sentence_transformers import CrossEncoder, SentenceTransformer

from app.evaluation.dataset import load_references
from experimental.u_10o0o.advanced_full_metrics import fact_coverage
from experimental.u_10o0o.advanced_pattern_full import DATA, input_files
from experimental.u_10o0o.advanced_patterns import (
    packed_context,
    score_variant,
    write_json,
)
from experimental.u_10o0o.advanced_search import (
    CONTEXT_TOKENS,
    FINAL_K,
    KST,
    RECORDS_DIR,
    fingerprint,
)
from experimental.u_10o0o.prepare import DATA_DIR, PROJECT_ROOT

PEER = PROJECT_ROOT / "experimental/ysl727727/overnight"
ARCHIVE = RECORDS_DIR / "advanced-factor-ablation-20261108/team-results.json"
PERSONAL = RECORDS_DIR / "advanced-industry-patterns-20261108/full-coverage"
GRAPH = "team_graph"
CONTROL = "team_diverse"
PERSONAL_ARMS = ("baseline", "fielded")
METRICS = (
    "doc_recall@20",
    "page_recall@20",
    "doc_recall@8",
    "page_recall@8",
    "quote_recall",
    "fact_recall",
    "all_facts",
    "context_tokens",
)


def load_peer(scratch, archive):
    sys.path.insert(0, str(PEER))
    import common

    common.OUT = scratch
    common.HERE = scratch
    common.LEVELS["adv"]["docs"] = [DATA_DIR / "documents"]
    import index
    import prepare
    import run

    prepare.main(["adv"])
    extraction = json.loads((scratch / "adv/extract_report.json").read_bytes())
    if (
        extraction["failed"]
        or len(extraction["files"]) != archive["pdf_count"]
        or sum(f["pages"] for f in extraction["files"]) != archive["page_count"]
    ):
        raise ValueError("동료 벤치와 같은 120개 PDF·18,000쪽이 필요합니다.")
    graph = run.preset(archive["presets"][GRAPH])
    control = run.preset(archive["presets"][CONTROL])
    dense = SentenceTransformer(
        archive["dense"]["model"],
        revision=archive["dense"]["revision"],
        device="cuda",
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    dense.max_seq_length = index.MODELS[graph["emb"]][3]
    index._loaded[graph["emb"]] = dense
    ix = run.get_index("adv", graph)
    if len(ix.rows) != archive["chunk_count"]:
        raise ValueError("동료 벤치와 p800 청크 수가 다릅니다.")
    rr = run.Reranker("adv")
    rr.models[graph["rerank"]] = CrossEncoder(
        archive["reranker"]["model"],
        revision=archive["reranker"]["revision"],
        max_length=archive["reranker"]["max_tokens"],
        device="cuda",
        local_files_only=True,
        model_kwargs={"dtype": torch.float16},
    )
    return index, run, ix, rr, graph, control


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups["all"].append(row)
        groups["overlap" if row["blind_related"] else "disjoint"].append(row)
        groups[f"type:{row['question_type']}"].append(row)
        groups[f"identifier:{row['identifier_mode']}"].append(row)
    summary = {}
    for name, group in groups.items():
        result = {"questions": len(group), "arms": {}}
        for arm in group[0]["arms"]:
            scores = [row["arms"][arm] for row in group]
            result["arms"][arm] = {
                **{key: sum(s[key] for s in scores) / len(scores) for key in METRICS},
                "all_facts_count": sum(s["all_facts"] for s in scores),
                "alternative_only_hits": sum(
                    s["alternative_only_hits"] for s in scores
                ),
            }
        graph_wins = Counter()
        for row in group:
            a = row["arms"]["fielded"]["all_facts"]
            b = row["arms"][GRAPH]["all_facts"]
            graph_wins[f"{a}:{b}"] += 1
        result["fielded_to_graph_all_facts"] = dict(sorted(graph_wins.items()))
        summary[name] = result
    return summary


def evaluate(output, scratch, log):
    questions, inputs = input_files()
    archived = json.loads(ARCHIVE.read_bytes())["config"]
    if any(fingerprint(PROJECT_ROOT / s["path"]) != s for s in archived["sources"]):
        raise ValueError("동료 코드가 보관된 벤치 버전과 다릅니다.")
    prior = json.loads((PERSONAL / "config.json").read_bytes())
    if inputs != prior["inputs"] or (prior["final_k"], prior["context_tokens"]) != (
        FINAL_K,
        CONTEXT_TOKENS,
    ):
        raise ValueError("기존 6,500문항 벤치와 입력 또는 문맥 조건이 다릅니다.")
    prior_rows = {
        r["id"]: r
        for line in (PERSONAL / "questions.jsonl").open(encoding="utf-8")
        if (r := json.loads(line))
    }
    if set(prior_rows) != {q["id"] for q in questions}:
        raise ValueError("기존 결과의 문항 ID가 다릅니다.")
    index, peer, ix, rr, graph, control = load_peer(scratch, archived)
    write_json(
        output / "config.json",
        {
            "started_at": datetime.now(KST).isoformat(),
            "inputs": inputs,
            "personal": {
                name: fingerprint(PERSONAL / name)
                for name in ("config.json", "questions.jsonl")
            },
            "team_archive": fingerprint(ARCHIVE),
            "runner": fingerprint(Path(__file__)),
            "team_sources": archived["sources"],
            "pdfs": archived["pdf_count"],
            "pages": archived["page_count"],
            "chunks": archived["chunk_count"],
            "dense": archived["dense"],
            "reranker": archived["reranker"],
            "gpu": torch.cuda.get_device_name(),
            "presets": {GRAPH: graph, CONTROL: control},
            "context_tokens": CONTEXT_TOKENS,
            "final_k": FINAL_K,
            "generation_calls": 0,
        },
    )
    log(f"동료 p800 색인 준비 완료 · {len(ix.rows):,}청크")
    prepared = [
        (row | {"file_name": row["book_id"] + ".pdf", "text": row["index_text"]}, 0.0)
        for row in ix.rows
    ]
    refs = None
    rows = []
    times = {GRAPH: [], CONTROL: []}
    pair_counts = Counter()
    with (output / "questions.jsonl").open("x", encoding="utf-8") as stream:
        for start in range(0, len(questions), 100):
            batch = questions[start : start + 100]
            vectors = index.embed_queries([q["question"] for q in batch], graph["emb"])
            packed = []
            for q, vector in zip(batch, vectors, strict=True):
                text = q["question"]
                before = rr.dirty
                t = perf_counter()
                order = peer.retrieve(ix, rr, control, text, vector)
                base_ms = (perf_counter() - t) * 1000
                pair_counts[CONTROL] += rr.dirty - before
                before = rr.dirty
                t = perf_counter()
                extended = peer.retrieve(ix, rr, graph, text, vector)
                graph_ms = base_ms + (perf_counter() - t) * 1000
                pair_counts[GRAPH] += rr.dirty - before
                times[CONTROL].append(base_ms)
                times[GRAPH].append(graph_ms)
                rr.cache.clear()
                candidates = {
                    CONTROL: [prepared[i] for i in order],
                    GRAPH: [prepared[i] for i in extended],
                }
                packed.append(
                    {
                        arm: (order, packed_context(order))
                        for arm, order in candidates.items()
                    }
                )
            if refs is None:
                refs = load_references(DATA / "references.json")
                if set(refs) != set(prior_rows):
                    raise ValueError("참고 정답과 기존 결과의 문항 ID가 다릅니다.")
            for q, contexts in zip(batch, packed, strict=True):
                qid = q["id"]
                reference = refs[qid]
                existing = prior_rows[qid]
                arms = {arm: existing["arms"][arm] for arm in PERSONAL_ARMS}
                for arm, (order, context) in contexts.items():
                    result = score_variant(q, order, context, reference)
                    arms[arm] = {
                        **result["metrics"],
                        **fact_coverage(reference, context),
                        "context_tokens": context.token_count,
                        "context_chunks": len(context.final_chunks),
                    }
                row = {
                    **{
                        key: existing[key]
                        for key in (
                            "id",
                            "question_type",
                            "identifier_mode",
                            "blind_related",
                        )
                    },
                    "arms": arms,
                }
                rows.append(row)
                stream.write(
                    json.dumps(
                        {**row, "arms": {arm: arms[arm] for arm in contexts}},
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            stream.flush()
            log(
                f"동료 설정 {len(rows):,}/{len(questions):,}문항 저장 · BGE {sum(pair_counts.values()):,}쌍"
            )
    write_json(output / "summary.json", summarize(rows))
    write_json(
        output / "runtime.json",
        {
            "shared_initial_bge_pairs": pair_counts[CONTROL],
            "graph_additional_bge_pairs": pair_counts[GRAPH],
            "timing_scope": "batch-embedded queries; pre-graph search plus incremental graph pass; context excluded",
            "arms": {
                arm: {
                    "p50_ms": sorted(samples)[(len(samples) - 1) // 2],
                    "p95_ms": sorted(samples)[(95 * len(samples) - 1) // 100],
                }
                for arm, samples in times.items()
            },
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise SystemExit("보관된 동료 벤치와 같은 CUDA FP16 환경이 필요합니다.")
    args.output.mkdir(parents=True, exist_ok=False)
    log_path = args.output / "run.log"

    def log(message):
        line = f"{datetime.now(KST).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    started = perf_counter()
    log("동료 ID 연결 재벤치 시작")
    with TemporaryDirectory(prefix="aden-peer-") as directory:
        evaluate(args.output, Path(directory), log)
    log(f"증강 6,500문항 완료 · 경과 {perf_counter() - started:.1f}초 · 외부 API 0회")


if __name__ == "__main__":
    main()
