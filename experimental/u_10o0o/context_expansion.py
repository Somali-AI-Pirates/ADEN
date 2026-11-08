"""Compare two Advanced generation contexts from frozen retrieval rankings."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openai import APIStatusError, OpenAI

from app.evaluation.graders import build_judge_prompt, load_prompt, parse_judge_output
from app.generation.chain import DEFAULT_PROMPT, generate_answer
from app.generation.citations import resolve_citations
from app.generation.context import build_context
from app.service.resources import _create_generation_model
from experimental.u_10o0o.context_evidence import _context_candidates, _count_tokens
from shared.settings import DEFAULT_CONFIG, PROJECT_ROOT, load_settings

RECORDS_DIR = Path(__file__).with_name("records")
BASELINE_DIR = RECORDS_DIR / "advanced-reranking-20261108-201548-983299"
KURE_PATH = BASELINE_DIR / "nlpai-lab-KURE-Reranker-nano/results.json"
RRF_PATH = BASELINE_DIR / "candidates/results.json"
MODEL_ID = "gpt-6-luna"
JUDGE_MAX_OUTPUT = 2048
TOKEN_BUDGET = 4000
WORKERS = 6
VARIANTS = ("kure3", "kure3_rrf2")
PRICING = {"generation": (0.10, 0.50), "judge": (0.10, 0.50)}


def read_json(path):
    return json.loads(path.read_bytes())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def verified_input(entry):
    path = PROJECT_ROOT / entry["path"]
    if digest(path) != entry["sha256"]:
        raise ValueError(f"보관된 입력과 SHA-256 불일치: {path.name}")
    return read_json(path)


def page_key(row):
    return row["book_id"], row["page"]


def load_cases():
    kure, rrf = read_json(KURE_PATH), read_json(RRF_PATH)
    if kure["inputs"] != rrf["inputs"] or kure["dataset"] != "advanced":
        raise ValueError("Advanced KURE와 RRF 실행의 입력이 일치해야 합니다.")
    inputs = kure["inputs"]
    pages = {
        (Path(page["file_name"]).stem, page["page"]): page
        for page in verified_input(inputs["pages"])
    }
    cases = []
    for split, files in inputs["evaluation"].items():
        questions = verified_input(files["questions"])
        references = {row["id"]: row for row in verified_input(files["references"])}
        for question, baseline, candidates in zip(
            questions, kure["records"][split], rrf["records"][split], strict=True
        ):
            question_id = question["id"]
            if baseline["question_id"] != question_id or candidates["question_id"] != question_id:
                raise ValueError("보관된 순위와 문항 순서가 다릅니다.")
            anchors = baseline["retrieved_pages"]
            anchor_keys = {page_key(row) for row in anchors}
            rrf_rows = candidates["retrieved_pages"]
            candidate_keys = {page_key(row) for row in rrf_rows}
            if len(anchors) != 3 or len(anchor_keys) != 3 or not anchor_keys <= candidate_keys:
                raise ValueError("KURE Top 3가 RRF 후보 집합과 일치하지 않습니다.")
            expanded = anchors + [row for row in rrf_rows if page_key(row) not in anchor_keys]
            contexts = tuple(
                build_context(
                    _context_candidates([(pages[page_key(row)], row["score"]) for row in ranked]),
                    k=k, token_budget=TOKEN_BUDGET, count_tokens=_count_tokens,
                )
                for ranked, k in ((anchors, 3), (expanded, 5))
            )
            if len(contexts[0].final_chunks) != 3 or (
                contexts[0].final_chunks != contexts[1].final_chunks[:3]
            ):
                raise ValueError("확장 문맥에서 기존 KURE Top 3를 모두 보존해야 합니다.")
            cases.append((split, question_id, question["question"].strip(), contexts, references[question_id]))
    if len(cases) != 60 or len({case[1] for case in cases}) != 60:
        raise ValueError("고정된 Advanced 문항 60개가 필요합니다.")
    return inputs, cases


def error_label(error):
    if isinstance(error, APIStatusError):
        code = error.code
        if code in ("insufficient_quota", "model_not_found", "rate_limit_exceeded"):
            return f"api_{error.status_code}_{code}"
        return f"api_{error.status_code}"
    return type(error).__name__


def usage_counts(usage):
    return {key: (usage or {}).get(key) for key in ("input_tokens", "output_tokens")}


def run_case(index, case, model, client, judge_template):
    split, question_id, question, contexts, reference = case
    gold_pages = {
        page_key(item) for group in reference["evidence_groups"] for item in group
    }
    generated, rows = {}, {}
    order = (0, 1) if index % 2 == 0 else (1, 0)
    for variant_index in order:
        variant, context = VARIANTS[variant_index], contexts[variant_index]
        row = {
            "split": split, "question_id": question_id, "variant": variant,
            "generated_first": variant_index == order[0],
            "multi_document": len({book_id for book_id, _ in gold_pages}) > 1,
            "context_tokens": context.token_count, "context_sha256": context.context_hash,
            "context_pages": len(context.final_chunks), "budget_excluded_pages": len(context.excluded_chunk_ids),
        }
        rows[variant] = row
        attempts = []
        started = perf_counter()
        try:
            result = generate_answer(question, context, model, max_attempts=1, attempt_log=attempts)
        except Exception as error:
            row.update(status="error", generation_error=error_label(error))
            row["generation_usage"] = usage_counts(attempts[-1] if attempts else None)
        else:
            row.update(status=result.model_answer.status, generation_error=None)
            row["generation_usage"] = usage_counts(result.usage)
            generated[variant] = result.model_answer
        row["generation_ms"] = round((perf_counter() - started) * 1000)

    for variant_index in order:
        variant, context = VARIANTS[variant_index], contexts[variant_index]
        if variant not in generated:
            continue
        answer = generated[variant]
        citations = resolve_citations(answer.source_ids, context.source_map)
        row = rows[variant]
        row["citation_count"] = len(citations)
        row["extra_citation_count"] = sum(c.source_id in ("S4", "S5") for c in citations)
        row["extra_gold_page_citations"] = sum(
            c.source_id in ("S4", "S5") and (c.book_id, c.page) in gold_pages for c in citations
        )
        record = {
            "question": question, "status": answer.status, "answer": answer.answer, "reason": answer.reason,
            "citations": [citation.model_dump(mode="json") for citation in citations],
            "final_context_chunks": [
                {**asdict(item.chunk), "source_id": f"S{rank}"}
                for rank, item in enumerate(context.final_chunks, start=1)
            ],
        }
        judge_prompt = build_judge_prompt(judge_template, record, reference)
        started = perf_counter()
        try:
            response = client.chat.completions.create(
                model=MODEL_ID,
                messages=[{"role": "user", "content": judge_prompt}],
                response_format={"type": "json_object"},
                reasoning_effort="low", max_completion_tokens=JUDGE_MAX_OUTPUT,
                store=False,
            )
            usage = response.usage
            row["judge_usage"] = {
                "input_tokens": usage.prompt_tokens if usage else None,
                "output_tokens": usage.completion_tokens if usage else None,
            }
            scores, claims = parse_judge_output(response.choices[0].message.content or "")
            row["judge"] = {
                criterion: {"value": score["value"], "status": score["status"]}
                for criterion, score in scores.items()
            }
            row["has_factual_claims"] = claims
            row["judge_error"] = None
        except Exception as error:
            row["judge_error"] = error_label(error)
        row["judge_ms"] = round((perf_counter() - started) * 1000)
    return [rows[variant] for variant in VARIANTS]


def check_smoke(rows):
    for row in rows:
        usage = (row.get("generation_usage"), row.get("judge_usage"))
        if (
            row.get("generation_error") or row.get("judge_error")
            or any(not item or None in item.values() for item in usage)
            or not any(score["status"] == "scored" for score in row.get("judge", {}).values())
        ):
            raise SystemExit("첫 문항의 생성·채점·토큰 사용량 확인 실패. 부분 기록을 확인하세요.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="OpenAI 유료 생성과 채점을 실행합니다.")
    args = parser.parse_args()
    inputs, cases = load_cases()
    if not args.run:
        print(f"Verified {len(cases)} question pairs at {TOKEN_BUDGET} tokens.")
        return

    settings = load_settings()
    if settings.generation_model != MODEL_ID:
        raise SystemExit(f"이 실험은 {MODEL_ID}만 사용합니다.")
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    stamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d-%H%M%S-%f")
    run_dir = RECORDS_DIR / f"advanced-context-expansion-{stamp}"
    run_dir.mkdir()
    judge_template = load_prompt("judge.txt")
    config = {
        "experiment": "Advanced 개인 실험",
        "owner": "u_10o0o",
        "variant_order": "Alternating within each question pair",
        "purpose": "Measure generation quality when adding two nonduplicate RRF pages",
        "questions": len(cases), "max_generation_calls": 2 * len(cases),
        "max_judge_calls": 2 * len(cases), "workers": WORKERS, "budget_usd": None,
        "estimated_usd": 0.12, "pricing_usd_per_million_input_output": PRICING,
        "stop_condition": "failed smoke or systemic API authentication, access, or rate error",
        "generation_model": settings.generation_model, "judge_model": MODEL_ID,
        "generation_max_output_tokens": settings.max_output_tokens,
        "judge_max_output_tokens": JUDGE_MAX_OUTPUT, "judge_reasoning_effort": "low",
        "context_tokenizer": "o200k_base", "context_budget": TOKEN_BUDGET,
        "generation_prompt_sha256": digest(DEFAULT_PROMPT),
        "judge_prompt_sha256": sha256(judge_template.encode()).hexdigest(),
        "basic_config_sha256": digest(DEFAULT_CONFIG), "runner_sha256": digest(Path(__file__)),
        "kure_results_sha256": digest(KURE_PATH), "rrf_results_sha256": digest(RRF_PATH),
        "inputs": inputs,
    }
    (run_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    print(f"Run directory: {run_dir.relative_to(PROJECT_ROOT)}", flush=True)
    with (
        ExitStack() as cleanup,
        OpenAI(timeout=settings.model_timeout_seconds, max_retries=0) as client,
        (run_dir / "results.jsonl").open("x", encoding="utf-8") as output,
        (run_dir / "run.log").open("x", encoding="utf-8") as log,
    ):
        model = _create_generation_model(settings, cleanup)

        def save(rows, completed):
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            errors = [row[key] for row in rows for key in ("generation_error", "judge_error") if row.get(key)]
            line = f"{datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')} {completed}/{len(cases)} pairs, errors={errors}"
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()
            return errors

        smoke = run_case(0, cases[0], model, client, judge_template)
        save(smoke, 1)
        check_smoke(smoke)
        with ThreadPoolExecutor(max_workers=WORKERS) as workers:
            for start in range(1, len(cases), WORKERS):
                batch = [
                    workers.submit(run_case, index, cases[index], model, client, judge_template)
                    for index in range(start, min(start + WORKERS, len(cases)))
                ]
                errors = []
                for index, future in enumerate(batch, start=start):
                    errors.extend(save(future.result(), index + 1))
                if any(error.startswith(("api_401", "api_403", "api_404", "api_429")) for error in errors):
                    raise SystemExit("API 오류로 중단했습니다. 부분 기록을 보존했습니다.")


if __name__ == "__main__":
    main()
