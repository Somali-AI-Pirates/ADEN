# RAG 정제 데이터

PDF에서 공통 규칙과 개별 기록을 분리한 **팀 검토용 데이터**다. Basic·Intermediate·Advanced를 서로 다른 폴더에 보존한다. 이 경로의 정제본은 Git으로 관리하고 원본 PDF·평가 정답·추출 캐시·검색 색인은 Git에서 제외한다.

```text
<dataset>/
├── rules.jsonl                 # 반복 규칙과 적용 조건, 출처 맥락
├── records/<원본 문서 ID>.jsonl # 표의 행, 거래 식별, 개별 원문 문단
├── cases.jsonl                 # 예약 등 원본 식별자 → 기록 연결
├── sources.jsonl               # 실제 PDF 파일·페이지·해시·제목
├── manifest.json               # 생성 코드·추출기·입력·산출물 버전
└── validation_report.json      # 처리 수량·본문 보존·검토 한계
```

## 사용과 검토

- `rules`는 검색 근거다. `scope=linked_source_context`이며 모델에게 내리는 시스템 지침이 아니다. 문구가 같은 경우에만 재사용하고 계약별 날짜 산입 차이는 별도 규칙으로 보존한다.
- `context_rule_ids`는 같은 원본 페이지에 있는 공통 규칙을 연결한다. 조건 충족이나 승인 효력 확정을 뜻하지 않는다. `related_rule_ids`는 주제상 관련된 검색 후보이며 실제 적용 여부는 별도로 확인한다.
- 표는 열 이름과 행별 값을 보존한다. 실제 사건일·등록일·예정일·서명일을 합치지 않고, 요청·검토·승인·철회·발행 상태도 원문에 적힌 값으로 둔다. `amounts`는 기재 값의 추출이며 최종 청구액 판정이 아니다. 미결은 `null`, 명시한 0은 `0`이다.
- Advanced 계약 표의 무료일수·누진 구간은 각 `records`의 `cost_terms`에 둔다. 기간 산입 방식은 해당 기록의 `context_rule_ids`로 연결한다. 비용 간 조건을 복사하거나 요율을 소급하지 않는다.
- `cases`는 같은 식별자를 언급하는 기록의 연결 색인이다. `resolution=references_only`이며 최신 상태·유효 승인·최종 금액을 자동 확정하지 않는다. Basic 상담 예시는 실제 예약번호가 없어 이 색인이 비어 있을 수 있다.
- 고객 인용문은 `external_quotation`으로 남기며 규칙으로 승격하지 않는다. 공격 문서를 삭제하거나 평가 정답·공격 라벨로 변환을 조정하지 않는다.
- `review_status=parsed`는 열·식별자 구조를 읽었다는 뜻이다. `needs_review` 문단은 의미 정규화가 남아 있다. `source_matched` 규칙은 확인한 원문 패턴과 일치한 결과이며 모든 거래에 대한 사람의 의미 검토가 끝났다는 뜻이 아니다.

스키마의 기준은 [curated_schema.py](../../app/ingestion/curated_schema.py)다. 상세 처리와 현재 한계는 [지식 데이터 구성](../../docs/design/knowledge-data.md)을 따른다.

## 생성과 재현

저장소 루트에서 설치한 Python 3.13 환경을 사용한다. 최초 추출에는 Poppler의 `pdftotext`가 필요하다. 외부 모델·API 키·벡터DB는 사용하지 않는다.

```bash
python -m app.ingestion.scripts.curate
python -m app.ingestion.scripts.curate --check
python -m app.ingestion.scripts.curate --check --render-search
```

특정 난이도만 처리하려면 `--datasets intermediate advanced`처럼 지정한다. 검색 입력은 `artifacts/knowledge/search/<dataset>/<build_id>/`에 생성되며 `search_records.jsonl`과 `search_rules.jsonl`을 분리한다. 원문 전체를 다시 넣어 공통 규칙을 중복시키지 않는다. 실제 임베딩과 서비스 연결은 후속 작업이다.

기존 정제본은 자동으로 덮어쓰지 않는다. 입력·코드가 바뀌면 `--output artifacts/curation-candidate` 같은 새 위치에서 생성하고 Git의 정제본과 비교·검토하여 반영한다. 사람이 정제본을 수정한 경우에도 재추출로 덮어쓰지 않는다. 생성 manifest는 최초 생성 버전과 해시를 보존하며, 이후 검토 수정의 이력은 Git 커밋으로 관리한다. `--check`는 현재 정제본의 스키마·참조를 검증한다.

행·출처 검증 통과와 전체 업무 의미 검토·실제 RAG 성능 검증은 구분한다. 불명확한 정정 관계와 승인 효력, 유효 시점, 비용 계산의 확정에는 원본과 적용 조건의 추가 확인이 필요하다.
