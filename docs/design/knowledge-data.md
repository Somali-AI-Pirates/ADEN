# RAG 지식 데이터 구성

수정일: 2026-11-06. 세 난이도의 **오프라인 정제 데이터 v2**와 기존 Basic 추출 결과 v1을 구분한다. 서비스·검색은 기존 Basic 구현을 유지하며, Intermediate·Advanced 정제본을 만들었다는 이유로 해당 난이도의 서비스가 완성된 것은 아니다.

## 기준 데이터와 파생 결과

| 대상 | 위치·Git 관리 | 용도 |
| --- | --- | --- |
| 모델 행동 지침 | `app/generation/prompts/`, 포함 | 기존 생성 코드가 적용하는 앱 지침 |
| 팀 검토용 규칙·개별 기록·출처 | `data/curated/<dataset>/`, 포함 | PDF 내용을 행·조건·참조로 정리한 RAG 지식 기준 |
| 제공 원본 PDF·평가 JSON | `data/<dataset>/documents`, `evaluation`, 제외 | 원본 보존·독립 평가. 정답은 정제에 사용하지 않음 |
| layout 추출 캐시 | `artifacts/knowledge/layout-cache/`, 제외 | 원본·추출기·텍스트 해시를 확인하고 재사용 |
| 검색 입력 | `artifacts/knowledge/search/<dataset>/<build_id>/`, 제외 | 정제본에서 재생성하는 검색 기록·규칙 |
| 임베딩·벡터 색인·실행 로그 | 기존 별도 저장소·`artifacts/`, 제외 | 검색·실행용 파생 결과 |

검토할 정제본 자체를 로컬 캐시로만 취급하지 않는다. `.gitignore`는 `data/curated/`의 정해진 JSONL·manifest·검증 결과만 허용하고 원본 PDF와 평가 정답은 계속 제외한다. 정제 데이터의 상세 파일·검토 규칙은 [정제 데이터 안내](../../data/curated/README.md)가 기준이다.

## 처리 방식

1. `documents/*.pdf`만 읽는다. 파일 수·페이지 수·원본 해시·실제 페이지 번호를 확인하고 추출 실패·빈 페이지는 오류로 처리한다. 평가 디렉터리는 읽지 않는다.
2. Basic/IS 형식의 표지·제목·절과 Advanced 계약 인계철 형식을 구별한다. Advanced의 PDF 안에 IS 원문 표기가 있어도 실제 파일 ID·PDF 페이지를 출처로 사용한다.
3. 표 제목과 데이터 행을 구별하고, 모든 행에서 비어 있는 열 경계를 찾아 줄바꿈된 값을 원래 열에 연결한다. 값·상태·날짜가 옆 행으로 섞이지 않도록 보존한다. 불명확하면 원문 검토 대상으로 남긴다.
4. 확인한 반복 업무 문장만 공통 규칙으로 묶는다. 적용 날짜·장비·비용·기산 방식이 다른 문장을 유사하다는 이유로 합치지 않는다. 공통 문장은 `records`에서 제거하고 같은 출처의 `context_rule_ids`로 연결한다.
5. 표 행에는 날짜·기재 금액·원문 상태를 별도 필드로 보존한다. Advanced 반복 계약의 무료일·누진 구간과 예약·장비·계약 식별도 읽는다. 예정·등록·실제 사건과 미결·확정 0을 구별한다.
6. 예약 등 식별자별 `cases` 색인을 만든다. 같은 이름의 화주나 최신 수신일만으로 사례를 합치거나 최종 상태를 확정하지 않는다.
7. 모든 본문 블록의 처리 여부와 출처·규칙·사례 연결을 검증한 뒤 정제 폴더를 게시한다. 기존 검토본을 자동으로 덮어쓰지 않는다.

이 처리에는 모델 호출·비용 계산·승인 효력 판정이 없다. 자동 추출이 성공한 행에도 업무 의미·유효 시점 검토가 필요하다. 기본 모델 지침은 PDF에서 생성하지 않는다. 고객 인용문과 모든 규칙·기록은 `retrieved_evidence`로 취급한다.

## 현재 데이터 계약

실제 모델은 `app/ingestion/curated_schema.py`가 기준이다. 런타임 `shared/contracts.py`의 Chunk·API·로그 계약은 바꾸지 않는다.

| 객체·파일 | 주요 필드 |
| --- | --- |
| `CuratedSource` / `sources.jsonl` | `source_id`, `dataset`, 실제 `book_id`·`file_name`·`file_sha256`·`page`, `heading`, 절 제목 |
| `CuratedRule` / `rules.jsonl` | `rule_id`, 원문 의미를 보존한 `text`, `kind`, `parameters`, `sources`, `scope=linked_source_context`, 검토 상태 |
| `CuratedRecord` / `records/<book>.jsonl` | `record_id`, 종류, `source_id`, layout 행 범위, 절, 열 이름별 `fields`, 원문 `text`, 식별자·사례 ID, 규칙 참조, 날짜·금액·원문 상태·계약 구간 |
| `CuratedCase` / `cases.jsonl` | `case_id`, 관련 `record_ids`, `resolution=references_only` |
| `manifest.json` | 스키마·변환 코드·추출기 버전, 입력 파일 해시·페이지, 최초 산출물 해시 |
| `validation_report.json` | 처리 수량, 본문 보존, 구조 검사와 의미 검토·런타임 검증 상태 구분 |

`context_rule_ids`는 같은 출처 페이지에 실제 있는 규칙이다. Basic의 `related_rule_ids`는 SI·VGM·예약 등 주제상 관련된 규칙 후보로, 적용 판정이나 인용 근거가 아니다. 승인·철회·정정 값은 `states`·`fields`와 원문에 보존하며 최신 효력 필드로 임의 변환하지 않는다.

Advanced의 `period_rule.parameters`는 보관료의 시작일 포함 여부·달력일/평일, 반납지연료의 시작일 간격·종료일 포함 여부를 구별한다. 요율과 무료일수는 해당 비용 행의 `cost_terms`에 남긴다. `cost_terms`는 `source_contract_row_only` 범위이며, 유효한 변경 승인 반영은 별도다.

Basic의 17개 규칙은 앞서 원문과 대조한 정의를 재사용한다. 기존 `app/ingestion/catalogs/basic.json`은 v1 생성기의 호환 입력으로 남아 있으며, v2 검색 입력의 기준은 Git의 정제 스냅샷이다. v1 CLI를 다시 실행한 결과를 검토한 v2 정제본에 자동 반영하지 않는다.

## 실행

Python 3.13에서 검증하며 최초 PDF 추출에는 설치된 Poppler `pdftotext`가 필요하다. `--help`·정제본 `--check`·검색 입력 렌더링은 원본 PDF나 추출기 없이 실행할 수 있다.

```bash
python -m app.ingestion.scripts.curate --help
python -m app.ingestion.scripts.curate
python -m app.ingestion.scripts.curate --datasets intermediate advanced --check
python -m app.ingestion.scripts.curate --check --render-search
```

`--data-root`는 기본 `data`, `--output`은 `data/curated`, `--cache`는 `artifacts/knowledge/layout-cache`다. 기존 정제본과 입력·코드가 다르면 새 `--output` 위치에서 후보를 만들어 비교한다. 출력 위치를 원본 데이터셋 폴더 안으로 지정할 수 없다.

## RAG DB 연결 경계

검색 입력은 `search_records.jsonl`과 `search_rules.jsonl`로 분리한다. 기록은 기존 `Chunk` 필드를 `chunk`에 담고 사례·공통 규칙 ID를 메타데이터로 연결한다. 규칙은 ID로 조회할 수 있도록 출처·조건을 보존한다. 여러 원문 전체를 다시 한데 넣어 공통 규칙을 중복시키지 않는다.

후속 검색 연결에서는 기록의 실제 출처와 연결 규칙, 관련 후보의 조건을 함께 확인해야 한다. `context_rule_ids`·`related_rule_ids`만으로 적용을 확정하거나 연결된 모든 출처를 검색 성공으로 계산하지 않는다. 추가 메타데이터를 기존 `Chunk(**payload)`에 그대로 전달하지 않는다.

이번 단계는 임베딩·Qdrant 적재·기본 서비스 전환을 실행하지 않는다. 실제 연결 전에는 임베딩 토큰 한도, 필터·규칙 동반 조회, 출처 인용, 동일 질문·모델·문맥 예산의 성능 비교가 필요하다. 업무 규칙은 검색·조회할 지식이며 시스템 메시지로 승격하지 않는다.

## 검증과 현재 한계

검사는 원문 블록 보존, 표 행·열, 규칙과 출처의 연결, 사례 색인, 조건·예외·미확정 보존, 재생성 일치와 기존 파일 보호를 대상으로 한다. 테스트 통과는 전체 데이터의 업무 의미 검토 완료나 실제 RAG 성능을 뜻하지 않는다.

공통 규칙은 확인한 문장 패턴과 명시적인 산입 조건을 대상으로 한다. 나머지 설명·정정 내역은 원문 문단으로 남기며 `needs_review`다. 문장 전체를 임의의 사실 필드로 추정하거나 모든 승인 관계를 자동 확정하지 않는다. 기존 Basic v1의 277개 안내·83개 페이지 묶음과 새 v2의 행·문단 수는 단위가 다르다.

이 보조 대화에서는 독립 에이전트 리뷰를 수행하지 않는다. 전체 의미 검토와 팀원의 PR 검토는 별도로 남아 있다.

[원본 배치·기존 색인](../../data/README.md) · [런타임 계약](contracts.md)
