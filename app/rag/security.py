"""Generate the four Advanced security answers through the current TableRAG."""

import argparse
import asyncio
import json
from pathlib import Path

from app.evaluation.dataset import load_questions
from app.evaluation.security import check_same_context
from app.rag.config import DEFAULT_DATA_DIRECTORY
from app.rag.service import RagService, load_service

DEFENSE_PATH = Path(__file__).resolve().parents[1] / "generation/prompts/defense.txt"


async def run_comparison(
    service: RagService,
    questions: list[dict[str, str]],
    *,
    experiment_id: str,
    defense: str,
) -> list[dict]:
    """Only id/question are passed to the service; evaluation labels stay out."""
    if len(questions) != 2 or len({q["id"] for q in questions}) != 2:
        raise ValueError("보안 질문은 서로 다른 ID의 두 문항이어야 합니다.")
    if not defense.strip() or any(not q["question"].strip() for q in questions):
        raise ValueError("질문과 방어 문장은 비어 있을 수 없습니다.")
    records = []
    for question in questions:
        pair = await service.acompare_security(question["question"], defense=defense)
        for record in pair:
            record.update(experiment_id=experiment_id, question_id=question["id"])
            records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--security-file", required=True)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--data-directory", default=str(DEFAULT_DATA_DIRECTORY))
    args = parser.parse_args()
    questions = load_questions(args.security_file)
    if len(questions) != 2 or len({q["id"] for q in questions}) != 2:
        parser.error("서로 다른 보안 질문 두 개를 지정하세요.")
    output = Path(args.output_directory)
    if output.exists():
        parser.error("기존 결과를 덮어쓰지 않도록 새 출력 디렉터리를 지정하세요.")
    defense = DEFENSE_PATH.read_text(encoding="utf-8")
    if not defense.strip():
        parser.error("방어 문장이 비어 있습니다.")

    async def run():
        service = load_service(args.data_directory)
        try:
            records = await run_comparison(
                service,
                questions,
                experiment_id=args.experiment_id,
                defense=defense,
            )
            return records, service.manifest
        finally:
            await service.aclose()

    records, service_manifest = asyncio.run(run())
    checks = check_same_context(records)
    payload_checks = {}
    for question in questions:
        pair = [r for r in records if r["question_id"] == question["id"]]
        payloads = [r.get("answer_payload") for r in pair]
        payload_checks[question["id"]] = (
            payloads[0] is not None
            and payloads[0] == payloads[1]
            and all(r["status"] != "error" for r in pair)
        )
    output.mkdir(parents=True, exist_ok=False)
    (output / "security.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "experiment_id": args.experiment_id,
                "service": service_manifest,
                "question_ids": [q["id"] for q in questions],
                "answers": len(records),
                "defense_file": str(DEFENSE_PATH),
                "context_checks": checks,
                "answer_payload_identical": payload_checks,
                "security_verdict": "not_evaluated",
                "latency_note": "base service time includes shared retrieval; compare answer attempts separately",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved: {output}")
    if any(r["status"] == "error" for r in records):
        raise SystemExit("생성 실패가 있습니다. 실패를 방어 성공으로 세지 마세요.")
    if not all(
        c["context_identical"] is True and c["generation_settings_identical"]
        for c in checks.values()
    ):
        raise SystemExit(
            "동일 문맥·생성 조건을 확인하지 못했습니다. 기록을 확인하세요."
        )
    if not all(payload_checks.values()):
        raise SystemExit("기본·방어의 전체 답변 입력이 다릅니다.")


if __name__ == "__main__":
    main()
