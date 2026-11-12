"""TableRAG 실행 기록(runs.jsonl)에서 원본 문서 본문을 뺀 공유용 상세 로그를 만든다(읽기 전용, 모델 호출 없음).

빼는 것과 대신 남기는 것:
- 검색 후보·최종 문맥·도구 발췌의 `text` → `text_sha256`, `text_chars`
- 모델 문맥 `context_text`·`model_context_text` → 글자 수(`*_chars`).
  해시는 원래 있는 `context_hash`·`model_context_hash`로 남는다
- 모델 호출 입력 `model_input`(시스템 프롬프트, planner 대화 이력, answer 입력, 도구 정의)과 `tool_schema`
  → 필드별 SHA-256·글자 수(목록은 항목 수 포함). 짧은 설정값 `context_hash`·`tool_choice`·`parallel_tool_calls`·
  `response_format`은 그대로 둔다

그 밖의 필드는 그대로 남긴다: 질문·답변·reason·인용, 상태, 검색 경로, 출처 ID·문서·페이지, 표 제목·열 정보,
DB 조회 결과 필드, planner 도구 호출(인자·중간 요약), 호출별 모델·시간·토큰·오류, 문맥 해시, SQL 결과, 진단 시간.
표 제목(`heading`, 원문 머리말 줄인 예약번호 / 화주 / 장비번호 / 문서 종류)과 구역 제목(`section`)은
원문에서 온 줄이지만 표를 식별하는 제목이라 남긴다.

    python -m experimental.Damsebi.strip_run_text <runs.jsonl> <출력 jsonl>

만든 뒤 원본 본문의 줄(30자 이상)이 답변·reason·중간 요약·표 제목 밖에 남았는지 검사하고, 남았으면 출력하지 않고 멈춘다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SHORT_INPUT_FIELDS = ("context_hash", "tool_choice", "parallel_tool_calls", "response_format")
CONTEXT_TEXT_FIELDS = ("context_text", "model_context_text")
# 원문 줄이 들어가도 검사에서 빼는 필드: 모델이 쓴 문장(답변·reason은 answers.jsonl에도 있다)과 표 식별 제목
QUOTE_FIELDS = ("answer", "reason", "interim_summary", "heading", "section")
MIN_LINE_CHARS = 30


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(value) -> dict:
    if isinstance(value, str):
        data = value
    else:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True)
    summary = {"sha256": _sha256(data), "chars": len(data)}
    if isinstance(value, list):
        summary["items"] = len(value)
    return summary


def strip_text(value):
    """모든 깊이의 `text` 문자열을 해시와 글자 수로 바꾼다."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "text" and isinstance(item, str):
                result["text_sha256"] = _sha256(item)
                result["text_chars"] = len(item)
            else:
                result[key] = strip_text(item)
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.append(strip_text(item))
        return result
    return value


def strip_model_input(model_input: dict) -> dict:
    result = {}
    for key, value in model_input.items():
        if key in SHORT_INPUT_FIELDS:
            result[key] = value
        else:
            result[key] = digest(value)
    return result


def strip_record(record: dict) -> dict:
    result = {}
    for key, value in record.items():
        if key in CONTEXT_TEXT_FIELDS:
            if value is None:
                result[f"{key}_chars"] = None
            else:
                result[f"{key}_chars"] = len(value)
        elif key == "model_input" and isinstance(value, dict):
            result[key] = strip_model_input(value)
        elif key == "attempts" and isinstance(value, list):
            attempts = []
            for attempt in value:
                stripped = {}
                for name, item in attempt.items():
                    if name == "model_input" and isinstance(item, dict):
                        stripped[name] = strip_model_input(item)
                    elif name == "tool_schema" and item is not None:
                        stripped[name] = digest(item)
                    else:
                        stripped[name] = strip_text(item)
                attempts.append(stripped)
            result[key] = attempts
        else:
            result[key] = strip_text(value)
    return result


def _source_lines(value, lines: set[str]) -> None:
    """원본 기록의 모든 `text`와 모델 문맥에서 검사할 본문 줄을 모은다."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key == "text" or key in CONTEXT_TEXT_FIELDS):
                for line in item.splitlines():
                    line = line.strip()
                    if len(line) >= MIN_LINE_CHARS:
                        lines.add(line)
            else:
                _source_lines(item, lines)
    elif isinstance(value, list):
        for item in value:
            _source_lines(item, lines)


def _shared_strings(value, strings: list[str]) -> None:
    """공유본의 문자열 중 모델 작성 필드(QUOTE_FIELDS)를 뺀 것을 모은다."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in QUOTE_FIELDS:
                continue
            _shared_strings(item, strings)
    elif isinstance(value, list):
        for item in value:
            _shared_strings(item, strings)
    elif isinstance(value, str):
        strings.append(value)


def leaked_lines(original: dict, stripped: dict) -> list[str]:
    lines = set()
    _source_lines(original, lines)
    strings = []
    _shared_strings(stripped, strings)
    shared = "\n".join(strings)
    leaked = []
    for line in sorted(lines):
        if line in shared:
            leaked.append(line)
    return leaked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("source", help="원본 runs.jsonl")
    parser.add_argument("out", help="본문을 뺀 출력 jsonl(이미 있으면 멈춤)")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"이미 있다(덮어쓰지 않음): {out}")
    lines = []
    for line in Path(args.source).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        original = json.loads(line)
        stripped = strip_record(original)
        leaked = leaked_lines(original, stripped)
        if leaked:
            raise SystemExit(f"{original.get('question_id')}: 본문 줄 {len(leaked)}개가 남았다. 예: {leaked[0][:60]}")
        lines.append(json.dumps(stripped, ensure_ascii=False))
    out.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    print(f"{len(lines)}건 → {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
