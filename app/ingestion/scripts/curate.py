"""공통 규칙·표 행·사례 참조를 Git 검토 데이터로 준비한다. 모델·DB에 연결하지 않는다."""

import argparse
import json
from pathlib import Path

from app.ingestion.curated_search import render_search
from app.ingestion.curation import EXPECTED, curate_dataset, validate_curated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", nargs="+", choices=list(EXPECTED), default=list(EXPECTED))
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("data/curated"))
    parser.add_argument("--cache", type=Path, default=Path("artifacts/knowledge/layout-cache"))
    parser.add_argument("--check", action="store_true", help="정제본의 스키마와 출처·규칙·사례 참조만 검증한다")
    parser.add_argument("--render-search", action="store_true", help="정제본에서 검색할 기록과 규칙 파일을 별도로 생성한다")
    parser.add_argument("--search-output", type=Path, default=Path("artifacts/knowledge/search"))
    args = parser.parse_args()
    for dataset in args.datasets:
        try:
            if args.check:
                result = validate_curated(args.output / dataset)
            else:
                print(f"{dataset}: 원본·표·규칙 처리 중", flush=True)
                _, result = curate_dataset(dataset, args.data_root, args.output, args.cache)
            if args.render_search:
                result["search_output"] = str(render_search(args.output / dataset, args.search_output))
        except (OSError, ValueError, RuntimeError) as error:
            parser.exit(1, f"{dataset} 정제 실패: {error}\n")
        print(json.dumps({"dataset": dataset, **result}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
