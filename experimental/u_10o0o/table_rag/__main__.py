"""Personal TableRAG commands; never changes the default API/search baseline."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "build",
            "vectors",
            "run",
            "score",
            "grade",
            "report",
            "timing",
            "baseline-answers",
            "workflow-run",
            "workflow-score",
            "workflow-grade",
            "workflow-report",
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--device", choices=("cpu", "shared_cuda", "cpu_gpu_rerank"), default="cpu"
    )
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.command.startswith("workflow-"):
        from . import workflow_experiment

        command = args.command.removeprefix("workflow-")
        if command == "run":
            workflow_experiment.run(args.output, args.device, args.limit)
        else:
            getattr(workflow_experiment, command)(args.output)
    elif args.command == "build":
        from .store import build

        build(args.output)
        from .oracle import audit

        audit(args.output)
    elif args.command == "vectors":
        from .retrieval import vectors

        vectors(args.output, args.device)
    elif args.command == "baseline-answers":
        from .benchmark import baseline_answers

        baseline_answers(args.output)
    elif args.command == "run":
        from .benchmark import run

        run(args.output, args.device, args.limit)
    elif args.command == "score":
        from .benchmark import score, telemetry

        score(args.output)
        telemetry(args.output)
    elif args.command == "grade":
        from .report import grade

        grade(args.output)
    elif args.command == "timing":
        from .report import timing

        timing(args.output, args.device)
    elif args.command == "report":
        from .report import report

        report(args.output)


if __name__ == "__main__":
    main()
