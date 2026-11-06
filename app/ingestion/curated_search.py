"""Git 정제본에서 검색 입력만 만든다. 임베딩·벡터DB·지침 적용은 하지 않는다."""

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from app.ingestion.curation import (
    json_text,
    read_rows,
    sha,
    validate_curated,
    write_rows,
)
from shared.contracts import Chunk


def render_search(curated_dir, output_root):
    curated_dir = Path(curated_dir).resolve()
    output_root = Path(output_root).resolve()
    if output_root.is_relative_to(curated_dir):
        raise ValueError("검색 입력은 정제본 폴더 바깥에 생성하세요.")
    validate_curated(curated_dir)
    manifest = json.loads((curated_dir / "manifest.json").read_text(encoding="utf-8"))
    dataset = manifest["identity"]["dataset"]
    inputs = {str(path.relative_to(curated_dir)): sha(path.read_bytes()) for path in sorted(curated_dir.rglob("*.json*"))}
    identity = {"dataset": dataset, "curated_files": inputs, "renderer_sha256": sha(Path(__file__).read_bytes())}
    target = output_root / dataset / sha(json_text(identity).encode())[:16]
    if target.exists():
        saved = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if saved["identity"] != identity or any(sha((target / name).read_bytes()) != digest for name, digest in saved["outputs"].items()):
            raise FileExistsError(f"기존 검색 입력을 보존합니다: {target}")
        return target
    sources = {row["source_id"]: row for row in read_rows(curated_dir / "sources.jsonl")}
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".search-", dir=target.parent) as folder:
        stage = Path(folder) / target.name
        stage.mkdir()

        def search_records():
            for path in sorted((curated_dir / "records").glob("*.jsonl")):
                for row in read_rows(path):
                    source = sources[row["source_id"]]
                    pieces = [row.get("text", ""), *(f"{key}: {value}" for key, value in row.get("fields", {}).items())]
                    chunk = Chunk(chunk_id=row["record_id"], book_id=source["book_id"], file_name=source["file_name"],
                                  page=source["page"], heading=source["heading"], text="\n".join(p for p in pieces if p))
                    yield {"schema_version": 2, "dataset": dataset, "chunk": asdict(chunk),
                           "case_ids": row.get("case_ids", []), "context_rule_ids": row.get("context_rule_ids", []),
                           "related_rule_ids": row.get("related_rule_ids", []),
                           "source_id": row["source_id"], "review_status": row["review_status"]}

        write_rows(stage / "search_records.jsonl", search_records())
        # 규칙은 record의 ID 참조로 조회한다. 출처·조건은 그대로 유지한다.
        write_rows(stage / "search_rules.jsonl", read_rows(curated_dir / "rules.jsonl"))
        saved = {"identity": identity, "embedding_executed": False,
                 "outputs": {name: sha((stage / name).read_bytes()) for name in ("search_records.jsonl", "search_rules.jsonl")}}
        (stage / "manifest.json").write_text(json_text(saved), encoding="utf-8", newline="\n")
        stage.rename(target)
    return target
