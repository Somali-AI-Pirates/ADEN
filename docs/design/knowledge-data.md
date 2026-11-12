# 정제 지식 데이터

Basic, Intermediate, Advanced 정제본은 원본 PDF에서 생성하는 오프라인 산출물이다. 기본 경로는 `artifacts/knowledge/curated/<dataset>/`이다. 기존 Basic 검색은 [Basic PDF 색인](../guides/data.md#qdrant-색인)을 사용한다. 기본 Advanced 서버의 SQLite·페이지·표 검색 자료는 [TableRAG 데이터 번들](../guides/data.md#advanced-tablerag-데이터)로 준비한다.

## 파일 구조와 사용 규칙

스키마는 [curated_schema.py](../../app/ingestion/curated_schema.py)에 정의한다.

| 파일 | 내용 |
| --- | --- |
| `sources.jsonl` | 원본 파일 ID·이름·해시·PDF 페이지·제목 |
| `rules.jsonl` | 확인된 반복 문구와 적용 조건·출처·검토 상태 |
| `records/<문서 ID>.jsonl` | 표 행과 문단, 원문·열별 필드·식별자·규칙 참조 |
| `cases.jsonl` | 원본 식별자에서 관련 기록으로 연결하는 참조 색인 |
| `manifest.json` | 스키마·추출기 버전, 입력과 최초 산출물의 해시 |
| `validation_report.json` | 처리 수량·본문 보존·검토 상태 |

- `context_rule_ids`는 같은 원본 페이지의 규칙, `related_rule_ids`는 주제상 관련 후보를 가리킨다. 규칙과 고객 인용문은 출처가 있는 검색 근거로 다룬다.
- 문구와 적용 조건이 같은 규칙만 묶는다. 비용·날짜 산입·장비 조건이 다르면 별도로 보존한다. 표의 행·열과 원문 상태를 보존하고 실제·등록·예정 일자를 구분한다. 미결 값은 `null`, 명시된 0은 `0`이다.
- Advanced 무료일·누진 요율은 해당 계약 행의 `cost_terms`에, 산입 방식은 연결된 규칙에 둔다. `cases`는 `resolution=references_only`이며 적용 승인·최종 상태·금액의 확정은 원본과 조건 확인이 필요하다.
- `parsed`는 구조 추출, `needs_review`는 의미 검토 필요, `source_matched`는 확인한 원문 패턴 일치를 뜻한다. 자동 검증은 구조·출처·참조의 일관성을 확인한다.

## 생성과 검사

최초 PDF 추출은 Poppler의 `pdftotext`를 사용한다. 생성한 정제본을 검증하거나 검색 입력으로 렌더링할 때는 정제본 파일만 있으면 된다.

```bash
python -m app.ingestion.scripts.curate --datasets basic
python -m app.ingestion.scripts.curate --datasets basic --check
python -m app.ingestion.scripts.curate --datasets basic --check --render-search
```

`--datasets`를 생략하면 세 난이도를 모두 처리한다. 별도 위치에 정제본을 만들거나 검증하려면 `--output`으로 해당 경로를 지정한다. 예를 들어 입력·코드가 바뀌면 `--output artifacts/curation-candidate`에 새 정제본을 만들고 기존 정제본과 비교할 수 있다. 기본 추출 캐시는 `artifacts/knowledge/layout-cache/`다. `prepare_knowledge`는 기존 Basic v1 산출물 재현용이다.

렌더링 결과는 `artifacts/knowledge/search/<dataset>/<build_id>/`의 `search_records.jsonl`과 `search_rules.jsonl`이다. 기록은 런타임 `Chunk` 필드를 `chunk`에 담고 규칙·사례 ID를 메타데이터로 분리한다. 서비스 연결 전에 임베딩 토큰 한도, 규칙 조회, 실제 페이지 인용, 같은 조건에서의 검색·답변 품질을 확인한다.
