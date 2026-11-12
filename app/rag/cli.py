"""Run the server's DB-first TableRAG and save its complete result."""

import argparse
import asyncio
import json
from uuid import uuid4

from app.rag.config import DEFAULT_DATA_DIRECTORY, DEFAULT_RUN_DIRECTORY
from app.rag.service import load_service


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the Advanced TableRAG service.")
    parser.add_argument("question")
    parser.add_argument("--data-directory", default=str(DEFAULT_DATA_DIRECTORY))
    args = parser.parse_args()
    request_id = uuid4().hex

    async def run() -> None:
        service = load_service(args.data_directory)
        try:
            result = await service.ainvoke(args.question, request_id=request_id)
            print(json.dumps(result.response.model_dump(mode="json"), ensure_ascii=False, indent=2))
            print(f"Saved run: {DEFAULT_RUN_DIRECTORY / request_id}")
        finally:
            await service.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
