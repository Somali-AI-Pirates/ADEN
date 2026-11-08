import argparse
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from importlib.metadata import version
from time import perf_counter
from typing import cast

import numpy as np

from experimental.u_10o0o.evaluate import RECORDS_DIR, SPLITS, evaluate, save_results
from experimental.u_10o0o.prepare import CACHE_PATH, DATA_DIR, DATASET
from experimental.u_10o0o.search import TOP_K

K1 = 1.5
B = 0.75
TOKENIZERS = ("bigram", "kiwi", "kiwi-id", "bigram-id")
IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9]{1,12}(?:-[A-Za-z0-9]{1,12}){1,4}(?![A-Za-z0-9_-])"
)
BIGRAM_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{0,6}(?:-[A-Z0-9]{1,8}){1,4}(?![A-Za-z0-9])"
)


def identifier_matches(text: str) -> list[re.Match[str]]:
    return [
        match
        for match in IDENTIFIER_PATTERN.finditer(text)
        if any(char.isalpha() for char in match[0])
        and any(char.isdigit() for char in match[0])
    ]


def tokenize(text: str) -> list[str]:
    parts = re.findall(r"[가-힣]+|[a-z0-9]+", text.lower())
    tokens = []

    for part in parts:
        if part.isascii() or len(part) == 1:
            tokens.append(part)
        else:
            for i in range(len(part) - 1):
                tokens.append(part[i : i + 2])

    return tokens


def tokenize_bigram_id(text: str) -> list[str]:
    """Keep the baseline terms and add each complete code as a search term."""
    return tokenize(text) + [match.lower() for match in BIGRAM_ID_PATTERN.findall(text)]


def load_tokenizer(name: str) -> tuple[Callable[[str], list[str]], dict[str, str]]:
    if name == "bigram":
        return tokenize, {"tokenizer": "korean_bigram_ascii_alphanumeric"}
    if name == "bigram-id":
        return tokenize_bigram_id, {
            "tokenizer": name,
            "identifier_pattern": BIGRAM_ID_PATTERN.pattern,
        }
    if name not in ("kiwi", "kiwi-id"):
        raise ValueError(f"지원하지 않는 BM25 토크나이저 {name!r}")

    from kiwipiepy import Kiwi, Token

    kiwi = Kiwi(num_workers=0)

    def tokenize_kiwi(text: str) -> list[str]:
        # Kiwi annotates string and batch inputs with one return type.
        tokens = cast(
            list[Token],
            kiwi.tokenize(
                text,
                pretokenized=[
                    (match.start(), match.end(), "SL")
                    for match in identifier_matches(text)
                ]
                if name == "kiwi-id"
                else None,
            ),
        )
        return [
            token.form.lower()
            for token in tokens
            if any(char.isalnum() for char in token.form)
        ]

    return tokenize_kiwi, {
        "tokenizer": name,
        "kiwipiepy_version": version("kiwipiepy"),
        "kiwipiepy_model_version": version("kiwipiepy-model"),
        **(
            {"identifier_pattern": IDENTIFIER_PATTERN.pattern}
            if name == "kiwi-id"
            else {}
        ),
    }


class BM25Index:
    def __init__(self, pages, tokenizer=tokenize):
        self.pages = pages
        self.postings = defaultdict(list)
        self.tokenizer = tokenizer

        page_lengths = []
        for page_index, page in enumerate(pages):
            tokens = self.tokenizer(page["text"])
            page_lengths.append(len(tokens))
            for term, frequency in Counter(tokens).items():
                self.postings[term].append((page_index, frequency))

        token_count = sum(page_lengths)
        if not token_count:
            raise ValueError("BM25 색인에 사용할 토큰이 없습니다.")
        average_length = token_count / len(pages)
        self.length_norms = [
            K1 * (1 - B + B * length / average_length) for length in page_lengths
        ]
        self.idf = {
            term: math.log1p((len(pages) - len(hits) + 0.5) / (len(hits) + 0.5))
            for term, hits in self.postings.items()
        }

    def search(self, question: str, *, top_k: int = TOP_K) -> list[tuple[dict, float]]:
        scores = defaultdict(float)
        query_terms = set(self.tokenizer(question))

        for term in sorted(query_terms & self.idf.keys()):
            weight = self.idf[term] * (K1 + 1)
            for page_index, frequency in self.postings[term]:
                scores[page_index] += (
                    weight * frequency / (frequency + self.length_norms[page_index])
                )

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [(self.pages[page_index], score) for page_index, score in ranked[:top_k]]


def main() -> None:
    parser = argparse.ArgumentParser(description="페이지 단위 BM25 검색 평가")
    parser.add_argument("--tokenizer", choices=TOKENIZERS, default="bigram")
    args = parser.parse_args()

    pages = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    started = perf_counter()
    tokenizer, tokenizer_runtime = load_tokenizer(args.tokenizer)
    tokenizer_description = "한글 두 글자 토큰과 영문·숫자 덩어리를 사용했다."
    if args.tokenizer == "kiwi":
        tokenizer_description = (
            "Kiwi 형태소를 소문자로 정규화하고, 글자와 숫자가 없는 토큰을 제외했다."
        )
    elif args.tokenizer == "kiwi-id":
        tokenizer_description = "영문과 숫자가 섞인 하이픈 식별자를 한 토큰으로 고정하고, 나머지는 Kiwi로 분석했다."
    elif args.tokenizer == "bigram-id":
        tokenizer_description = (
            "Bigram 토큰에 대문자로 시작하는 하이픈 코드를 통째로 추가했다."
        )

    index = BM25Index(pages, tokenizer=tokenizer)
    index_seconds = perf_counter() - started
    warmup_path = DATA_DIR / "evaluation" / f"{SPLITS[0]}_questions.json"
    warmup = json.loads(warmup_path.read_text(encoding="utf-8"))[:3]
    for item in warmup:
        index.search(item["question"])

    times = []

    def search_question(question):
        started = perf_counter()
        ranked = index.search(question)
        times.append((perf_counter() - started) * 1000)
        return ranked

    result = evaluate(
        f"BM25 ({args.tokenizer})", None, search_question, similarity="bm25"
    )
    latency = {
        "scope": "question_tokenization_and_bm25_top3",
        "warmup_queries": len(warmup),
        "query_count": len(times),
        "p50_ms": float(np.median(times)),
        "p95_ms": float(np.percentile(times, 95)),
    }
    result["runtime"] = {
        "device": "cpu",
        "page_count": len(pages),
        **tokenizer_runtime,
        "k1": K1,
        "b": B,
        "index_build_seconds": index_seconds,
        "search_latency": latency,
    }
    run_at = datetime.fromisoformat(result["run_at"])
    run_id = (
        f"{DATASET}-bm25-{args.tokenizer}-page-top{TOP_K}-{run_at:%Y%m%d-%H%M%S-%f}"
    )
    output_dir = RECORDS_DIR / run_id
    output_dir.mkdir(parents=True)
    summary = save_results(result, output_dir)
    with (output_dir / "report.md").open("a", encoding="utf-8") as report_file:
        report_file.write(
            "\n## CPU 실행 조건\n\n"
            f"{tokenizer_description} "
            f"BM25의 k1은 {K1}, b는 {B}다.\n"
            f"{len(pages):,}쪽의 색인 생성에 {index_seconds:.2f}초가 걸렸다. "
            f"색인 생성 후 {len(warmup)}문항으로 예열하고 {len(times)}문항을 한 번씩 검색했다. "
            f"질문 토큰화부터 상위 {TOP_K}쪽 선택까지 p50은 {latency['p50_ms']:.1f}ms, "
            f"p95는 {latency['p95_ms']:.1f}ms였다.\n"
        )
    print(summary)
    print(f"저장 위치 {output_dir}")


if __name__ == "__main__":
    main()
