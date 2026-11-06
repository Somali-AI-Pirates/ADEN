"""PDF 원문 → Git에서 검토하는 규칙·행별 기록·출처. 외부 모델 호출은 없다."""

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from app.ingestion.common_rules import match_common_rule
from app.ingestion.curated_schema import (
    CuratedCase,
    CuratedRecord,
    CuratedRule,
    CuratedSource,
)
from app.ingestion.knowledge import DEFAULT_CATALOG
from app.ingestion.layout import (
    CASE_ID,
    compact,
    cost_terms,
    field_values,
    header_cells,
    identifiers,
    looks_like_row,
    page_body,
    parse_table,
    readable,
)

EXPECTED = {"basic": (15, 360), "intermediate": (50, 3750), "advanced": (120, 18000)}
PREFIX = {"basic": "SB", "intermediate": "IS", "advanced": "AS"}
PARSER_VERSION = "curated-knowledge-v2"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_rows(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            if hasattr(row, "model_dump"):
                model = row
                row = model.model_dump(exclude_defaults=True)
                for field in ("scope", "trust", "resolution"):
                    if hasattr(model, field):
                        row[field] = getattr(model, field)
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_rows(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


@dataclass
class LayoutDocument:
    book_id: str
    file_name: str
    file_sha256: str
    pages: list[str]


def load_layout(path, cache, tool_version):
    """검증 가능한 PDF·추출기·텍스트 해시가 모두 맞을 때만 캐시를 재사용한다."""
    digest = sha(path.read_bytes())
    text_path = cache / f"{path.stem}-{digest[:16]}.txt"
    meta_path = text_path.with_suffix(".json")
    identity = {"pdf_sha256": digest, "extractor": tool_version, "mode": "layout"}
    valid = False
    if text_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        valid = meta.get("identity") == identity and meta.get("text_sha256") == sha(text_path.read_bytes())
    if not valid:
        result = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True)
        text_path.write_bytes(result.stdout)
        meta_path.write_text(json_text({"identity": identity, "text_sha256": sha(result.stdout)}), encoding="utf-8", newline="\n")
    pages = text_path.read_text(encoding="utf-8").split("\f")
    if not pages[-1].strip():
        pages.pop()
    if not pages or any(not page.strip() for page in pages):
        raise ValueError(f"빈 PDF 또는 빈 페이지: {path.name}")
    return LayoutDocument(path.stem, path.name, digest, pages)


def basic_rules():
    """기존에 원문과 대조한 17개 규칙을 새 검토 데이터 형식으로 연결한다."""
    catalog = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    result = {}
    for item in catalog["rules"]:
        sources = sorted({f"basic:{a['book_id']}:p{a['page']}" for a in item["anchors"]})
        result[item["rule_id"]] = CuratedRule(
            rule_id=item["rule_id"], text=item["text"], kind="business_rule", sources=sources,
            parameters={key: item[key] for key in ("scope", "conditions", "effect", "exceptions") if key in item},
            review_status="source_checked",
        )
    return result


def related_basic_rules(text, rule_ids):
    """주제상 관련된 검색 후보다. 적용 조건 충족을 확정하지 않는다."""
    topics = []
    for pattern, names in (
        (r"SI|선적지시서", ("si-deadline", "si-submission")),
        (r"VGM", ("vgm-",)), (r"예약", ("booking-confirmation",)),
        (r"반입", ("gate-", "actual-gate",)), (r"초안|발행", ("draft-approval",)),
        (r"실제 출항|출항 완료", ("actual-departure",)), (r"도착|인도", ("eta-vs",)),
    ):
        if re.search(pattern, text):
            topics.extend(names)
    return sorted(rule_id for rule_id in rule_ids if any(name in rule_id for name in topics))


def split_page(dataset, document, page_number, rules):
    text = document.pages[page_number - 1]
    source_id = f"{dataset}:{document.book_id}:p{page_number}"
    heading, parts = page_body(text, document.book_id, page_number, len(document.pages))
    source = CuratedSource(source_id=source_id, dataset=dataset, book_id=document.book_id,
                           file_name=document.file_name, file_sha256=document.file_sha256,
                           page=page_number, heading=heading)
    # 단일 거래가 제목에 명시된 경우에만 페이지의 개별 행에 그 거래 맥락을 연결한다.
    heading_cases = [item for item in identifiers(heading) if CASE_ID.match(item)]
    inherited = heading_cases if len(heading_cases) == 1 else []
    records, stats, covered = [], Counter(), set()
    context_rules = {key for key, rule in rules.items() if source_id in rule.sources} if dataset == "basic" else set()
    section, index = heading, 0

    def cover(block):
        location = (block["start"], block["end"])
        if location in covered:
            raise ValueError(f"중복 처리한 원문 블록: {source_id} {location}")
        covered.add(location)

    def record(block, kind, fields=None, raw_text="", parsed=False):
        content = " ".join((raw_text, *list((fields or {}).values())))
        ids = identifiers(content + " " + section)
        cases = sorted({*inherited, *(item for item in ids if CASE_ID.match(item))})
        dates, amounts, states = field_values(fields or {})
        records.append(CuratedRecord(
            record_id=f"{source_id}:l{block['start']}", kind=kind, source_id=source_id,
            line_start=block["start"], line_end=block["end"], section=section,
            fields=fields or {}, text=raw_text, identifiers=ids, case_ids=cases,
            dates=dates, amounts=amounts, states=states, cost_terms=cost_terms(fields or {}),
            review_status="parsed" if parsed else "needs_review",
        ))

    while index < len(parts):
        part = parts[index]
        if len(part["lines"]) > 1 and re.search(r" / .+외 \d+건\s*$", part["lines"][0]):
            section = readable(part["lines"][0])
            source.sections.append(section)
            part["lines"] = part["lines"][1:]
            part["start"] += 1
            stats["inline_section_lines"] += 1
        raw = "\n".join(part["lines"])
        if index == 0 and inherited and inherited[0].startswith("HG") and "화주 " in readable(raw):
            terms = identifiers(raw)
            context = heading.rsplit(" > ", 1)[-1].split(" / ")
            contracts = [item for item in terms if item.startswith("CT-")]
            if len(context) == 4 and len(contracts) == 1:
                record(part, "case_context", fields={"예약": context[0], "화주": context[1],
                       "장비": context[2], "계약": contracts[0], "기록 종류": context[3]},
                       raw_text=readable(raw), parsed=True)
                stats["case_contexts"] += 1
                cover(part)
                index += 1
                continue
        if (len(part["lines"]) == 1 and index + 1 < len(parts) and header_cells(parts[index + 1])):
            section = readable(part["lines"][0])
            source.sections.append(section)
            stats["section_blocks"] += 1
            cover(part)
            index += 1
            continue
        if header_cells(part):
            end = index + 1
            while end < len(parts) and looks_like_row(parts[end]) and not header_cells(parts[end]):
                end += 1
            if end > index + 1:
                row_blocks = parts[index + 1:end]
                cover(part)
                for row in row_blocks:
                    cover(row)
                table = parse_table(part, row_blocks)
                stats["tables"] += 1
                if table is not None:
                    for row, fields in zip(row_blocks, table):
                        record(row, "table_row", fields=fields, parsed=True)
                    stats["table_rows"] += len(table)
                else:
                    # 애매한 표는 원래 열 배치를 보존한 검토 대상으로 남긴다.
                    record(part, "narrative", raw_text="\n".join(part["lines"]))
                    for row in row_blocks:
                        record(row, "narrative", raw_text="\n".join(row["lines"]))
                    stats["ambiguous_tables"] += 1
                index = end
                continue
        external = bool(re.match(r"\s*\[.*(?:화주|고객).*인용", raw))
        external = external or (dataset == "basic" and document.book_id == "SB15")
        match = None if external or dataset == "basic" else match_common_rule(raw)
        if match:
            rule_id = f"{dataset}.rule.{sha(compact(raw).encode())[:16]}"
            if rule_id not in rules:
                rules[rule_id] = CuratedRule(rule_id=rule_id, sources=[source_id],
                                            review_status="source_matched", **match)
            elif source_id not in rules[rule_id].sources:
                rules[rule_id].sources.append(source_id)
            context_rules.add(rule_id)
            stats["factored_rule_blocks"] += 1
        else:
            record(part, "external_quotation" if external else "narrative", raw_text=readable(raw))
            stats["narrative_blocks"] += 1
            if looks_like_row(part):
                stats["unparsed_layout_blocks"] += 1
        index += 1
        cover(part)
    if covered != {(part["start"], part["end"]) for part in parts}:
        raise ValueError(f"처리되지 않은 원문 블록: {source_id}")
    for item in records:
        item.context_rule_ids = sorted(context_rules)
        if dataset == "basic":
            item.related_rule_ids = related_basic_rules(item.text + " ".join(item.fields.values()), rules)
    stats["body_blocks"] = len(parts)
    stats["records"] = len(records)
    stats["contract_rows"] = sum(bool(item.cost_terms) for item in records)
    stats["covered_blocks"] = len(covered)
    return source, records, stats


def transform_documents(dataset, documents, output):
    """전체 페이지를 처리하며 기록은 원본 파일별로 나눠 저장한다."""
    rules = basic_rules() if dataset == "basic" else {}
    sources, cases, counts = [], defaultdict(list), Counter()
    output.joinpath("records").mkdir()
    for document in documents:
        records = []
        for page_number in range(1, len(document.pages) + 1):
            source, page_records, page_counts = split_page(dataset, document, page_number, rules)
            sources.append(source)
            counts.update(page_counts)
            records.extend(page_records)
            for record in page_records:
                for case_id in record.case_ids:
                    cases[case_id].append(record.record_id)
        write_rows(output / "records" / f"{document.book_id}.jsonl", records)
    write_rows(output / "sources.jsonl", sources)
    write_rows(output / "rules.jsonl", [rules[key] for key in sorted(rules)])
    write_rows(output / "cases.jsonl", [CuratedCase(case_id=key, record_ids=sorted(set(value))) for key, value in sorted(cases.items())])
    report = {"dataset": dataset, "documents": len(documents), "pages": len(sources), "rules": len(rules),
              "cases": len(cases), **dict(counts), "review": "행·원문 구조와 참조를 검증함. 업무 효력 판정과 전체 의미 검토는 별도 필요"}
    report["quality_gate"] = {"source_coverage": "passed", "references": "passed",
                              "full_semantic_review": "pending", "runtime_rag": "not_run"}
    (output / "validation_report.json").write_text(json_text(report), encoding="utf-8", newline="\n")
    return report


def validate_curated(path):
    """원본 PDF 없이도 Git의 정제본 스키마와 참조를 확인한다."""
    path = Path(path)
    source_rows, rule_rows = list(read_rows(path / "sources.jsonl")), list(read_rows(path / "rules.jsonl"))
    sources = {row["source_id"]: CuratedSource.model_validate(row) for row in source_rows}
    rules = {row["rule_id"]: CuratedRule.model_validate(row) for row in rule_rows}
    if len(sources) != len(source_rows) or len(rules) != len(rule_rows):
        raise ValueError("중복 출처 또는 규칙 ID가 있습니다.")
    ids, record_count = set(), 0
    memberships = defaultdict(set)
    for file in sorted((path / "records").glob("*.jsonl")):
        for row in read_rows(file):
            record = CuratedRecord.model_validate(row)
            if record.record_id in ids or record.source_id not in sources or record.line_end < record.line_start:
                raise ValueError(f"중복·출처·행 범위 오류: {record.record_id}")
            ids.add(record.record_id)
            record_count += 1
            for case_id in record.case_ids:
                memberships[case_id].add(record.record_id)
            for rule_id in record.context_rule_ids:
                if rule_id not in rules or record.source_id not in rules[rule_id].sources:
                    raise ValueError(f"다른 출처 맥락의 규칙 참조: {record.record_id}")
            if any(rule_id not in rules for rule_id in record.related_rule_ids):
                raise ValueError(f"없는 관련 규칙: {record.record_id}")
    for rule in rules.values():
        if any(source_id not in sources for source_id in rule.sources):
            raise ValueError(f"없는 규칙 근거: {rule.rule_id}")
    case_ids = set()
    for row in read_rows(path / "cases.jsonl"):
        case = CuratedCase.model_validate(row)
        if case.case_id in case_ids or set(case.record_ids) != memberships[case.case_id]:
            raise ValueError(f"사례와 기록의 연결이 일치하지 않습니다: {case.case_id}")
        case_ids.add(case.case_id)
    if case_ids != set(memberships):
        raise ValueError("사례 색인에 누락된 기록 연결이 있습니다.")
    return {"sources": len(sources), "rules": len(rules), "records": record_count}


def curate_dataset(dataset, data_root, output_root, cache_root, *, progress=None):
    """고정된 난이도 원본 전체를 정제한다. 기존 검토 스냅샷을 덮어쓰지 않는다."""
    if dataset not in EXPECTED:
        raise ValueError(f"지원하지 않는 데이터셋: {dataset}")
    if not shutil.which("pdftotext"):
        raise RuntimeError("Poppler pdftotext가 필요합니다. 이미 생성한 정제본은 원본 없이 검증할 수 있습니다.")
    data_root, output_root, cache_root = (Path(p).resolve() for p in (data_root, output_root, cache_root))
    if output_root == data_root or output_root.is_relative_to(data_root / dataset) or cache_root.is_relative_to(data_root / dataset):
        raise ValueError("정제본·캐시는 원본 데이터셋 폴더 바깥에 두세요.")
    pdfs = sorted((data_root / dataset / "documents").glob("*.pdf"))
    expected_docs, expected_pages = EXPECTED[dataset]
    names = {f"{PREFIX[dataset]}{number:0{3 if dataset == 'advanced' else 2}d}.pdf" for number in range(1, expected_docs + 1)}
    if {p.name for p in pdfs} != names:
        raise ValueError(f"{dataset} PDF 목록이 예상한 {expected_docs}개와 다릅니다.")
    tool = subprocess.run(["pdftotext", "-v"], capture_output=True, check=True).stderr.decode().splitlines()[0]
    cache = cache_root / dataset
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        documents = list(pool.map(lambda p: load_layout(p, cache, tool), pdfs))
    if sum(len(doc.pages) for doc in documents) != expected_pages:
        raise ValueError(f"{dataset} 페이지 수가 예상한 {expected_pages}와 다릅니다.")
    if dataset == "basic":
        catalog = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        by_book = {doc.book_id: doc for doc in documents}
        for profile in catalog["documents"]:
            if by_book[profile["book_id"]].file_sha256 != profile["sha256"]:
                raise ValueError("Basic 원본이 검토한 규칙 정의의 해시와 다릅니다.")
        for rule in catalog["rules"]:
            for anchor in rule["anchors"]:
                page_text = by_book[anchor["book_id"]].pages[anchor["page"] - 1]
                if compact(anchor["quote"]) not in compact(page_text):
                    raise ValueError(f"Basic 규칙 근거를 확인할 수 없습니다: {rule['rule_id']}")
    identity = {"schema_version": 2, "dataset": dataset, "parser": PARSER_VERSION, "extractor": tool,
                "pydantic_version": version("pydantic"),
                "parser_sha256": sha(b"".join(Path(__file__).with_name(name).read_bytes() for name in
                                             ("curation.py", "curated_schema.py", "layout.py", "common_rules.py"))),
                "documents": [{"file_name": doc.file_name, "sha256": doc.file_sha256, "pages": len(doc.pages)} for doc in documents]}
    if dataset == "basic":
        identity["basic_catalog_sha256"] = sha(DEFAULT_CATALOG.read_bytes())
    target = output_root / dataset
    if target.exists():
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if manifest["identity"] != identity:
            raise FileExistsError(f"기존 검토본을 보존합니다. 새 --output 경로에서 생성·비교하세요: {target}")
        for name, digest in manifest["outputs"].items():
            if sha((target / name).read_bytes()) != digest:
                raise FileExistsError(f"수정된 검토본을 보존합니다. 새 --output 경로를 사용하세요: {target / name}")
        validate_curated(target)
        return target, json.loads((target / "validation_report.json").read_text(encoding="utf-8"))
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".curating-", dir=output_root) as folder:
        stage = Path(folder) / dataset
        stage.mkdir()
        report = transform_documents(dataset, documents, stage)
        validate_curated(stage)
        manifest = {"identity": identity, "snapshot_id": sha(json_text(identity).encode())[:16],
                    "outputs": {str(p.relative_to(stage)): sha(p.read_bytes()) for p in sorted(stage.rglob("*")) if p.is_file()}}
        (stage / "manifest.json").write_text(json_text(manifest), encoding="utf-8", newline="\n")
        stage.rename(target)
    if progress:
        progress(report)
    return target, report
