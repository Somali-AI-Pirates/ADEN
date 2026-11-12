"""Pure Korean bigram and complete-code tokenization for BM25."""

import re

TOKEN = re.compile(r"[가-힣]+|[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Split Korean runs into overlapping bigrams; preserve ASCII runs."""
    tokens = []
    for part in TOKEN.findall(text.lower()):
        if part.isascii() or len(part) == 1:
            tokens.append(part)
        else:
            tokens.extend(part[i : i + 2] for i in range(len(part) - 1))
    return tokens


BM25_CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]{0,6}"
    r"(?:[ \t]*[-‐‑–—−－][ \t]*[A-Za-z0-9]{1,8}){1,4}(?![A-Za-z0-9])"
)
BM25_CODE_DASHES = str.maketrans({dash: "-" for dash in "‐‑–—−－"})


def tokenize_for_bm25(text: str) -> list[str]:
    """Return bigrams plus normalized complete codes, preserving old order."""
    codes = [
        re.sub(r"[ \t]*-[ \t]*", "-", match.group().translate(BM25_CODE_DASHES)).lower()
        for match in BM25_CODE_PATTERN.finditer(text)
    ]
    return tokenize(text) + codes
