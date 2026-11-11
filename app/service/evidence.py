"""저장 근거로 A 색인 없이 API와 평가 어댑터를 연결한다."""
import json
import os
from dataclasses import fields
from pathlib import Path

import tiktoken

from app.generation.context import build_context
from shared.contracts import Chunk, RetrievedChunk
from shared.exceptions import ServiceError

CHUNK_FIELDS = [field.name for field in fields(Chunk)]


def stored_provider(runs_path, settings, *, with_context=False):
    """JSONL의 (질문, 방식)별 후보 또는 저장된 최종 청크 순서를 재생한다."""
    stored = {}
    for line in Path(runs_path).read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            run = json.loads(line)
            stored.setdefault(run["question"], {})[run["retrieval_mode"]] = run
    encoding = tiktoken.get_encoding(settings.context_tokenizer)

    def provider(question, mode):
        run = stored[question].get(mode)
        if run is None or run.get("retrieved_candidates") is None:
            raise NotImplementedError(f"저장된 후보가 없는 검색 방식: {mode}")
        candidates = [
            RetrievedChunk(Chunk(**{k: row[k] for k in CHUNK_FIELDS if k in row}),
                           row["rank"], row["score"], row["retriever"])
            for row in run["retrieved_candidates"]
        ]
        if not with_context:
            return candidates, None
        by_id = {item.chunk.chunk_id: item for item in candidates}
        final = []
        for row in run["final_context_chunks"]:
            item = by_id[row["chunk_id"]]
            saved = Chunk(**{k: row[k] for k in CHUNK_FIELDS if k in row})
            if item.chunk != saved:
                raise ValueError("저장된 후보와 최종 청크의 내용이 다릅니다.")
            final.append(item)
        context = build_context(final, settings.retrieval.k,
                                settings.retrieval.context_token_budget,
                                lambda text: len(encoding.encode(text)))
        if len(context.final_chunks) != len(final):
            raise ValueError("저장된 최종 문맥을 k·토큰 한도나 중복 제거로 변경할 수 없습니다.")
        return candidates, context

    return provider


def stored_evidence(settings):
    """ADEN_EVIDENCE_PROVIDER=app.service.evidence:stored_evidence 팩터리."""
    runs_path = os.environ.get("ADEN_EVIDENCE_RUNS", "").strip()
    if not runs_path or not Path(runs_path).is_file():
        raise ServiceError("ADEN_EVIDENCE_RUNS에 재생할 실행 기록(runs.jsonl) 경로를 지정해 주세요.", 503)
    return stored_provider(runs_path, settings,
                           with_context=bool(os.environ.get("ADEN_EVIDENCE_CONTEXT")))
