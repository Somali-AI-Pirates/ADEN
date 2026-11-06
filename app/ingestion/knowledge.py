"""Basic 원본에서 지식 데이터를 준비한다. 모델·벡터DB·평가 정답을 사용하지 않는다."""

import hashlib
import json
import re
import tempfile
from dataclasses import asdict
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from app.ingestion.chunker import FOOTER, chunk_pages, index_text
from app.ingestion.knowledge_schema import (
    BasicCatalog,
    BusinessRule,
    CaseRecord,
    EvidenceBlock,
    GuidanceExcerpt,
    IndexEntry,
    IndexMetadata,
    SourceRef,
)
from app.ingestion.loader import extract_pages
from shared.contracts import Chunk

DEFAULT_CATALOG = Path(__file__).resolve().parent / "catalogs" / "basic.json"
PARSER_VERSION = "basic-knowledge-v1"
CASE_TITLE = re.compile(r"사례|상담 기록")
ARTIFACT_NAMES = {
    "pages.jsonl", "blocks.jsonl", "rules.jsonl", "guidance.jsonl", "records.jsonl", "chunks.jsonl",
    "index_entries.jsonl", "answer_policy.json", "schemas.json", "validation_report.json",
}


def _compact(text):
    return re.sub(r"\s+", "", text)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def answer_policy():
    """프롬프트를 복제하거나 변경하지 않고 현재 기준 위치와 선택 조건만 기록한다."""
    root = files("app.generation").joinpath("prompts")
    return {
        "schema_version": 1,
        "indexed": False,
        "source": "application_code",
        "instructions_from_documents": False,
        "prompts": [
            {"path": f"app/generation/prompts/{name}", "sha256": _sha(root.joinpath(name).read_bytes()),
             "activation": activation}
            for name, activation in (("shipping_qa.txt", "base"), ("defense.txt", "defense_opt_in"))
        ],
    }


def _blocks_for_page(page, profile):
    lines = page.text.splitlines()
    nonempty = [i for i, line in enumerate(lines) if line.strip()]
    if len(nonempty) < 5:
        raise ValueError(f"Basic 페이지 구조를 확인할 수 없습니다: {page.book_id} p{page.page}")
    header, title, section = [lines[i].strip() for i in nonempty[:3]]
    if not header.startswith(page.book_id + " |") or not FOOTER.fullmatch(lines[nonempty[-1]]):
        raise ValueError(f"지원하지 않는 PDF 페이지 구조: {page.book_id} p{page.page}")
    if [int(n) for n in re.findall(r"\d+", lines[nonempty[-1]])] != [page.page, profile["pages"]]:
        raise ValueError(f"페이지 꼬리말과 원본 위치가 다릅니다: {page.book_id} p{page.page}")
    heading = f"[{header}] {title} > {section}"
    body = "\n".join(lines[nonempty[2] + 1:nonempty[-1]]).strip()
    blocks = []
    for number, text in enumerate(re.split(r"\n\s*\n", body), start=1):
        text = text.strip()
        if not text:
            continue
        block_id = f"basic:{page.book_id}:p{page.page}:b{number}"
        source = SourceRef(
            book_id=page.book_id, file_name=page.file_name, file_sha256=profile["sha256"],
            page=page.page, block_id=block_id,
        )
        blocks.append(EvidenceBlock(
            block_id=block_id, heading=heading, text=text,
            source_origin=profile["source_origin"], contains_quotation=bool(re.search(r'[“”"]', text)),
            source=source,
        ))
    if not blocks:
        raise ValueError(f"본문이 없는 페이지: {page.book_id} p{page.page}")
    return section, blocks


def _structured_rules(definitions, by_page):
    rules = []
    for definition in definitions:
        sources = []
        for anchor in definition["anchors"]:
            blocks = by_page.get((anchor["book_id"], anchor["page"]), [])
            matched = [block for block in blocks if _compact(anchor["quote"]) in _compact(block.text)]
            if len(matched) != 1:
                raise ValueError(f"규칙 근거를 유일하게 확인할 수 없습니다: {definition['rule_id']}")
            if matched[0].source_origin != "carrier_guidance":
                raise ValueError("고객 문의를 검토된 업무 규칙으로 승격할 수 없습니다.")
            if matched[0].source not in sources:
                sources.append(matched[0].source)
        # 현재 Chunk의 인용 계약은 한 문서·한 페이지다. 여러 페이지를 합친 주장을 만들지 않는다.
        if len({(s.book_id, s.page) for s in sources}) != 1:
            raise ValueError("구조화 규칙은 현재 한 페이지의 근거로 완결되어야 합니다.")
        rules.append(BusinessRule(
            **{key: value for key, value in definition.items() if key != "anchors"},
            normalization="structured", review_status="source_checked", sources=sources,
        ))
    return rules


def _rule_search_text(rule):
    parts = [rule.title, rule.text]
    for label, value in (("범위", rule.scope), ("조건", [c.model_dump() for c in rule.conditions]),
                         ("효과", rule.effect), ("예외", rule.exceptions)):
        if value:
            parts.append(f"{label}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
    return "\n".join(parts)


def _source_chunks(pages, by_page, entities, max_chars):
    entries = []
    for page in pages:
        key = (page.book_id, page.page)
        blocks = by_page[key]
        spans, offset = [], 0
        for block in blocks:
            end = offset + len(_compact(block.text))
            spans.append((offset, end, block.source))
            offset = end
        body = "".join(_compact(block.text) for block in blocks)
        cursor = 0
        for chunk in chunk_pages([page], max_chars):
            content = _compact(chunk.text)
            begin = body.find(content, cursor)
            if not content or begin != cursor:
                raise ValueError(f"청크의 원문 위치를 확인할 수 없습니다: {chunk.chunk_id}")
            end = begin + len(content)
            sources = [source for left, right, source in spans if left < end and right > begin]
            cursor = end
            kind, entity_id = entities[key]
            entries.append(IndexEntry(chunk=chunk, embedding_text=index_text(chunk), metadata=IndexMetadata(
                content_kind=kind, entity_id=entity_id, sources=sources,
            )))
        if cursor != len(body):
            raise ValueError(f"검색 입력에 보존되지 않은 본문: {page.book_id} p{page.page}")
    return entries


def transform_pages(pages, catalog, max_chars=800):
    """페이지 → 근거·규칙·사례·검색 입력. 모든 값은 검증 후 파일에 기록한다."""
    profiles = {item["book_id"]: item for item in catalog["documents"]}
    by_page, entities, guidance, records = {}, {}, [], []
    for page in pages:
        if page.book_id not in profiles:
            raise ValueError(f"출처 분류가 없는 PDF: {page.book_id}")
        key = (page.book_id, page.page)
        if key in by_page:
            raise ValueError(f"중복 페이지: {key}")
        section, blocks = _blocks_for_page(page, profiles[page.book_id])
        by_page[key] = blocks
        text = "\n\n".join(block.text for block in blocks)
        sources = [block.source for block in blocks]
        if profiles[page.book_id]["source_origin"] == "customer_inquiry_collection" or CASE_TITLE.search(section):
            record_id = f"basic:{page.book_id}:p{page.page}:case"
            record_type = "external_inquiry" if profiles[page.book_id]["source_origin"] == "customer_inquiry_collection" else "illustrative_case"
            records.append(CaseRecord(record_id=record_id, title=blocks[0].heading,
                                      record_type=record_type, text=text, sources=sources))
            entities[key] = ("case_record", record_id)
        else:
            guidance_id = f"basic:{page.book_id}:p{page.page}:guidance"
            guidance.append(GuidanceExcerpt(
                guidance_id=guidance_id, title=blocks[0].heading, text=text, sources=sources,
            ))
            entities[key] = ("source_guidance", guidance_id)
    rules = _structured_rules(catalog["rules"], by_page)
    entries = _source_chunks(pages, by_page, entities, max_chars)
    for rule in rules:
        source = rule.sources[0]
        chunk = Chunk(chunk_id=rule.rule_id, book_id=source.book_id, file_name=source.file_name,
                      page=source.page, text=_rule_search_text(rule),
                      heading=by_page[(source.book_id, source.page)][0].heading)
        if len(index_text(chunk)) > max_chars:
            raise ValueError(f"조건과 예외를 보존한 규칙이 검색 입력 길이를 초과합니다: {rule.rule_id}")
        entries.append(IndexEntry(chunk=chunk, embedding_text=index_text(chunk), metadata=IndexMetadata(
            content_kind="structured_rule", entity_id=rule.rule_id, sources=rule.sources,
        )))
    blocks = [block for page_blocks in by_page.values() for block in page_blocks]
    ids = ([item.guidance_id for item in guidance] + [rule.rule_id for rule in rules]
           + [record.record_id for record in records])
    if len(ids) != len(set(ids)) or len({e.chunk.chunk_id for e in entries}) != len(entries):
        raise ValueError("지식 또는 검색 입력 ID가 중복됩니다.")
    referenced = {s.block_id for unit in [*guidance, *rules, *records] for s in unit.sources}
    indexed = {s.block_id for entry in entries for s in entry.metadata.sources}
    if referenced != {b.block_id for b in blocks} or indexed != referenced:
        raise ValueError("분류·검색 입력에서 누락된 원문 문단이 있습니다.")
    report = {
        "status": "passed", "pages": len(pages), "blocks": len(blocks), "rules": len(rules),
        "structured_rules": len(rules), "source_only_guidance": len(guidance),
        "case_records": len(records), "search_units": len(entries), "unreferenced_blocks": 0,
        "classification": "Basic 출처 역할·페이지 제목에 따른 원문 분류; 사례 내용의 사실 여부는 미판정",
        "limitations": ["source_only 안내는 조건·효과 정규화와 내용 검토가 남아 있음",
                        "상담·가정 사례는 실제 예약 원장이 아니며 actual_state는 null",
                        "임베딩 토큰 수·검색 품질·모델 답변·원격 색인은 검증하지 않음"],
    }
    return blocks, guidance, rules, records, entries, report


def _verify_existing(path, identity):
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest["identity"] != identity or set(manifest["outputs"]) != ARTIFACT_NAMES:
        raise ValueError(f"기존 빌드의 구성과 다릅니다: {path}")
    for name, digest in manifest["outputs"].items():
        if _sha((path / name).read_bytes()) != digest:
            raise ValueError(f"기존 산출물이 변경되었습니다. 새 출력 위치를 사용하세요: {path / name}")
    return manifest


def prepare_knowledge(data_dir, output_root, *, catalog_path=DEFAULT_CATALOG, max_chars=800):
    """검증된 Basic만 변환하고 원본·기존 빌드를 덮어쓰지 않는다."""
    data_dir, output_root, catalog_path = Path(data_dir).resolve(), Path(output_root).resolve(), Path(catalog_path)
    if output_root.is_relative_to(data_dir):
        raise ValueError("출력 위치는 원본 데이터 폴더 바깥이어야 합니다.")
    if max_chars < 100:
        raise ValueError("max_chars는 100 이상이어야 합니다.")
    catalog_bytes = catalog_path.read_bytes()
    catalog = BasicCatalog.model_validate_json(catalog_bytes).model_dump()
    pdfs = sorted((data_dir / "documents").glob("*.pdf"))
    profiles = {item["book_id"]: item for item in catalog["documents"]}
    if {pdf.name for pdf in pdfs} != {f"{book_id}.pdf" for book_id in profiles}:
        raise ValueError("Basic PDF 목록이 catalog와 다릅니다. 원본을 확인하세요.")
    for pdf in pdfs:
        if _sha(pdf.read_bytes()) != profiles[pdf.stem]["sha256"]:
            raise ValueError(f"검토한 원본과 해시가 다릅니다. 규칙과 출처를 재검토하세요: {pdf.name}")
    policy = answer_policy()
    identity = {
        "schema_version": 1, "dataset": "basic", "parser": PARSER_VERSION,
        "pypdf": version("pypdf"), "max_chars": max_chars,
        "catalog_sha256": _sha(catalog_bytes),
        "code_sha256": _sha(b"".join(Path(__file__).with_name(name).read_bytes()
                                     for name in ("knowledge.py", "knowledge_schema.py", "loader.py", "chunker.py"))),
        "inputs": [{"file_name": p.name, "sha256": profiles[p.stem]["sha256"]} for p in pdfs],
        "answer_policy": policy,
    }
    build_id = _sha(_json(identity).encode())[:16]
    target = output_root / build_id
    if target.exists():
        return target, _verify_existing(target, identity)
    pages, extraction = extract_pages(pdfs, extraction_mode="layout")
    if extraction["failed"] or extraction["empty_pages"]:
        raise ValueError("PDF 추出 실패 또는 빈 페이지가 있습니다. 산출물을 게시하지 않습니다.")
    counts = {item["file_name"]: item["pages"] for item in extraction["files"]}
    if any(counts.get(f"{book_id}.pdf") != profile["pages"] for book_id, profile in profiles.items()):
        raise ValueError("예상한 PDF 페이지 수와 다릅니다.")
    blocks, guidance, rules, records, entries, report = transform_pages(pages, catalog, max_chars)
    report["documents"] = len(pdfs)
    outputs = {
        "pages.jsonl": _jsonl([asdict(page) for page in pages]),
        "blocks.jsonl": _jsonl([b.model_dump() for b in blocks]),
        "rules.jsonl": _jsonl([r.model_dump() for r in rules]),
        "guidance.jsonl": _jsonl([g.model_dump() for g in guidance]),
        "records.jsonl": _jsonl([r.model_dump() for r in records]),
        "chunks.jsonl": _jsonl([asdict(e.chunk) for e in entries]),
        "index_entries.jsonl": _jsonl([e.model_dump() for e in entries]),
        "answer_policy.json": _json(policy),
        "schemas.json": _json({cls.__name__: cls.model_json_schema()
                               for cls in (SourceRef, EvidenceBlock, BusinessRule, GuidanceExcerpt, CaseRecord, IndexEntry)}),
        "validation_report.json": _json(report),
    }
    manifest = {"build_id": build_id, "identity": identity,
                "outputs": {name: _sha(content.encode("utf-8")) for name, content in outputs.items()}}
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".preparing-", dir=output_root) as staging:
        stage = Path(staging) / build_id
        stage.mkdir()
        for name, content in outputs.items():
            (stage / name).write_text(content, encoding="utf-8", newline="\n")
        (stage / "manifest.json").write_text(_json(manifest), encoding="utf-8", newline="\n")
        if target.exists():
            _verify_existing(target, identity)
        else:
            stage.rename(target)
    return target, manifest
