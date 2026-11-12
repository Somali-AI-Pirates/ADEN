"""PDF page extraction and curated source normalization."""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from app.ingestion.curated_schema import CuratedRecord, CuratedSource
from app.ingestion.curation import read_rows

from .constants import CODE


def page_id(source):
    return f"{source['book_id']}:p{int(source['page']):04d}"


def family(source):
    heading = source["heading"]
    found = set()
    if re.search(r"HG20\d{2}-", heading):
        found.add("HG")
    if re.search(r"HA28-", heading):
        found.add("HA")
    if re.match(r"IS\d+\s*\|", heading):
        found.add("IS")
    if any(code.startswith(("SW28-", "DL28-")) for code in CODE.findall(heading)):
        found.add("other")
    return next(iter(found)) if len(found) == 1 else "unknown"


def extract_pages(documents):
    pages = []
    for pdf in sorted(Path(documents).glob("*.pdf")):
        for number, page in enumerate(PdfReader(pdf).pages, 1):
            raw = page.extract_text(extraction_mode="layout") or ""
            blocks = re.split(r"\n\s*\n", raw.strip())
            restored = []
            for block in blocks:
                text = ""
                for line in block.splitlines():
                    line = " ".join(line.split())
                    if not line:
                        continue
                    if text.endswith((".", ",", ")", "?", "!", ":", "”", "’")):
                        text += " "
                    text += line
                if text:
                    restored.append(text)
            pages.append(
                {"file_name": pdf.name, "page": number, "text": "\n\n".join(restored)}
            )
    if not pages:
        raise ValueError(f"No PDFs found under {documents}")
    return pages


def typed(raw, label):
    value = raw.strip()
    state = next(
        (
            s
            for s in ("검토 필요", "미결", "해당 없음", "미정", "확인 필요", "미확정")
            if s in value
        ),
        "reported",
    )
    if state != "reported":
        return {"value": None, "unit": None, "state": state, "type": "TEXT"}
    if value.startswith(label):
        value = value[len(label) :].lstrip(" :：")
    date_match = re.fullmatch(
        r"(\d{4})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})일?", value
    )
    if date_match:
        try:
            parsed_date = date(*(int(part) for part in date_match.groups()))
        except ValueError:
            parsed_date = None
        if parsed_date:
            return {
                "value": parsed_date.isoformat(),
                "unit": "date",
                "state": state,
                "type": "DATE",
            }
    number = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    signed_number = rf"[+-]?{number}"
    measure = re.fullmatch(
        rf"(?:(KRW|USD|EUR)\s*({signed_number})|({signed_number})\s*(원|달러|KRW|USD|EUR|kg|KG|톤|일|대|개|%))",
        value,
    )
    if measure:
        number = measure[2] or measure[3]
        unit = measure[1] or measure[4]
        unit = {"원": "KRW", "달러": "USD", "KG": "kg"}.get(unit, unit)
        return {
            "value": str(Decimal(number.replace(",", ""))),
            "unit": unit,
            "state": state,
            "type": "DECIMAL",
        }
    if re.fullmatch(signed_number, value) and not any(
        x in label for x in ("번호", "코드", "예약")
    ):
        return {
            "value": str(Decimal(value.replace(",", ""))),
            "unit": "",
            "state": state,
            "type": "DECIMAL",
        }
    return {"value": None, "unit": None, "state": state, "type": "TEXT"}


def _read_curated(curated):
    sources = {
        row["source_id"]: CuratedSource.model_validate(row).model_dump()
        for row in read_rows(curated / "sources.jsonl")
    }
    records = []
    for path in sorted((curated / "records").glob("*.jsonl")):
        records.extend(
            CuratedRecord.model_validate(row).model_dump() for row in read_rows(path)
        )
    return sources, records


def page_references(pages_path):
    pages = json.loads(Path(pages_path).read_text(encoding="utf-8"))
    references = set()
    texts = {}
    for page in pages:
        book_id = Path(page["file_name"]).stem
        source = {"book_id": book_id, "page": page["page"]}
        reference = page_id(source)
        if reference in references:
            raise ValueError(f"Duplicate source page: {reference}")
        references.add(reference)
        texts[reference] = page["text"]
    return references, texts
