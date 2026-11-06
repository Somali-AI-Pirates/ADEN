"""원본을 보존하며 Basic 지식 JSONL을 준비한다. 외부 서비스 연결은 없다."""

import argparse
import json
from pathlib import Path

from app.ingestion.knowledge import DEFAULT_CATALOG, prepare_knowledge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/basic"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/knowledge/basic"))
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--max-chars", type=int, default=800)
    args = parser.parse_args()
    try:
        path, manifest = prepare_knowledge(args.data, args.output, catalog_path=args.catalog, max_chars=args.max_chars)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"데이터 준비 실패: {error}\n")
    report = json.loads((path / "validation_report.json").read_text(encoding="utf-8"))
    print(json.dumps({"output": str(path), "build_id": manifest["build_id"], **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
