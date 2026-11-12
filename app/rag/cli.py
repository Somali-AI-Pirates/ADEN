"""Run the server's DB-first TableRAG and save its complete result."""

import argparse
import asyncio
import json
from uuid import uuid4

from app.rag.config import load_table_rag_settings
from app.rag.service import load_service


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the Advanced TableRAG service.")
    parser.add_argument("question")
    parser.add_argument("--data-directory")
    args = parser.parse_args()
    settings = load_table_rag_settings(data_directory=args.data_directory)
    request_id = uuid4().hex

    async def run() -> None:
        service = await load_service(settings=settings)
        try:
            result = await service.ainvoke(args.question, request_id=request_id)
            print(json.dumps(result.response.model_dump(mode="json"), ensure_ascii=False, indent=2))
            print(f"Saved run: {settings.run_directory / request_id}")
        finally:
            await service.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
