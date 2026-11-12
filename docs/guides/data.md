# 데이터 배치와 색인

## 원본 배치

| 데이터셋 | PDF 경로 | 수량 |
| --- | --- | --- |
| Basic | `data/basic/documents/SB01.pdf`~`SB15.pdf` | 15개, 각 24쪽 |
| Intermediate | `data/intermediate/documents/IS01.pdf`~`IS50.pdf` | 50개, 3,750쪽 |
| Advanced | `data/advanced/documents/AS001.pdf`~`AS120.pdf` | 120개, 18,000쪽 |

Basic은 제공 ZIP 두 개의 PDF를 합치고, 두 ZIP에 공통인 평가 JSON 8개는 `data/basic/evaluation/`에 한 벌만 둔다. `sha256sum data/basic/documents/*.pdf`의 결과를 [Basic 문서 카탈로그](../../app/ingestion/catalogs/basic.json)의 `documents[].sha256`과 비교한다. 파일명은 원본을 유지한다.

- `{development,additional,expansion_validation}_questions.json`에는 실행용 `id`와 `question`이 있다.
- 같은 이름에서 `_questions`를 뺀 JSON은 채점용 정답·근거다. 보안 비교 파일은 `security_questions.json`과 `security_tasks.json`이다. 대응 파일 지정과 입력 점검은 [평가 가이드](c-module-guide.md#실제-연결)를 따른다.
- `custom_questions.json`·`custom.json`은 팀이 해당 난이도 PDF에서 작성한 평가 세트다(S 단일 근거, M 복수 근거). 제공 자료와 달리 팀원이 같은 기준으로 채점하도록 질문과 기대 답변·출처를 모두 Git으로 추적한다. `custom.json`은 채점 단계에서만 읽고 검색·생성 입력에 넣지 않는다. Basic 결과와 파일 hash는 [reports/custom-2026-11-07](../../reports/custom-2026-11-07/analysis.md)에 있다.

색인·정제 입력은 해당 난이도의 `documents/*.pdf`다. 원본 PDF·평가 자료·[정제본](../design/knowledge-data.md)·추출 캐시·색인·전체 실행 기록은 로컬에서 관리한다. 보안 시험용 문구는 데이터로 처리한다.

## Advanced TableRAG 데이터

기본 번들 위치는 artifacts/advanced-table-rag다. 원본 PDF부터 만드는 명령과 모델 준비는 [빠른 시작](quickstart.md#2-sqlite와-검색-자료-준비)을 따른다. build는 복원 페이지, 파싱된 표, HG·HA·IS 코드·역할 연결, 모델 벡터와 manifest를 만들고 check는 결과의 지문과 연결을 검사한다.

| 파일 | 역할 |
| --- | --- |
| records.sqlite3 | 공통 대상·코드·속성·역할·원본 연결, 표 카탈로그 검색 |
| tables.sqlite3 | 계산용 물리 표와 행·열·원본 출처 |
| pages.json, table-chunks.json, catalog.json | 복원 페이지와 검증된 표 검색 단위 |
| vectors.npy | 문서 순서와 일치하는 정규화 벡터 |
| corpus.json, store.json, records.json | 파일·원본·모델 지문과 스키마·구축 정보 |

원본 페이지 18,000개와 IS 표 본문 3,750개는 같은 2,048차원 벡터 배열의 행에 대응해 총 21,750개 벡터를 이룬다. 검색 단위는 서로 달라도 같은 원본 페이지를 가리킬 수 있다. 최종 출처는 book_id와 page로 중복 제거한다.

```python
from app.rag.corpus import load_corpus

corpus = load_corpus("artifacts/advanced-table-rag")
```

corpus.documents와 corpus.vectors는 같은 순서를 사용한다. corpus.pages_by_source는 원본 출처 조회, corpus.table_store는 스키마와 제한된 읽기 전용 SQL을 제공한다. 직접 로드했다면 사용 후 corpus.close()로 벡터 mmap을 닫는다. 공통 RecordIndex는 코드·대상별 조회를 담당한다.

완성된 번들의 모든 파일을 함께 옮기고 check로 검증한다. 원본 PDF·DB·벡터·실행 기록은 로컬 artifacts에서 관리한다. 데이터나 모델을 변경하면 지문이 갱신되며, 중단된 구축의 재개에는 동일한 입력·구축 코드 지문을 사용한다. 단계 파일 분리로 기존 중간 캐시의 코드 지문이 달라진 경우, 이전 코드로 재개하거나 새 작업 경로에 구축한다. 완성된 번들은 이전 생성 코드 지문이 달라도 기존 형식·파일·모델·정렬 검사에 통과하면 읽을 수 있다.

## Advanced TableRAG Qdrant Cloud 벡터 검색

기본 실행은 `numpy`로 `vectors.npy`를 검색한다. YAML에 `table_rag.vector_backend: qdrant`를 지정하면 dense Top 50 검색만 Qdrant Cloud의 exact 검색(`exact=True`)으로 수행한다. ANN과 양자화 검색은 사용하지 않는다. 주소는 `connections.qdrant_url`에, 인증값은 `.env`의 `QDRANT_API_KEY`에 둔다. 코드에서 backend를 생략했을 때의 기본값은 `numpy`여서 오프라인·기존 로컬 실행 경로를 유지한다.

기본 컬렉션은 `advanced_table_rag`다. 다른 컬렉션 이름은 `advanced_table_rag` 또는 `advanced_table_rag_` 접두사를 가진 이름만 허용한다. 업로드·검사 명령은 서버와 같은 `table_rag.collection`을 읽으며, 명시한 `--collection`이 우선한다. 서버가 사용할 컬렉션과 같은 이름으로 검사한다. Basic의 기존 Qdrant 컬렉션은 사용하거나 변경하지 않는다.

기존 번들의 21,750개 2,048차원 벡터와 검색 단위 ID·출처 식별값을 업로드한다. 이 단계는 KURE-v3 재임베딩을 하지 않으며 PDF 원문, 검색 텍스트, SQLite DB를 Cloud로 보내지 않는다. 로컬 번들과 `vectors.npy`는 NumPy 검색, 데이터 검사·재사용을 위해 유지한다. BM25 문서, 질문 임베딩, KURE nano 리랭커, 원문 페이지·표와 SQLite도 로컬에 남는다. Cloud는 dense 후보를 찾는 역할만 하며, BM25와 5:5 RRF·리랭킹·최종 출처 흐름은 그대로다.

저장소 루트에서 업로드한 뒤 같은 번들로 검사를 실행한다.

```bash
uv run --locked python -m app.ingestion.scripts.prepare_table_rag upload-qdrant --directory artifacts/advanced-table-rag
uv run --locked python -m app.ingestion.scripts.prepare_table_rag check-qdrant --directory artifacts/advanced-table-rag --full
```

일반 `check-qdrant`는 컬렉션의 벡터 크기·거리 함수·데이터 지문과 준비 상태, 점 개수를 확인한다. `--full`은 모든 점을 읽어 벡터와 검색 단위 ID·출처 대응을 로컬 코퍼스와 대조한다. 이미 `ready` 상태인 동일 컬렉션에 `upload-qdrant`를 다시 실행하면 점 개수와 ID·출처 payload를 확인한 뒤 업로드를 건너뛰고 재사용한다. 같은 지문의 중단된 `building` 컬렉션은 업로드를 재개한다. 기존 컬렉션이 현재 데이터 지문·벡터 크기·거리 함수와 맞지 않으면 중단하며 컬렉션을 삭제하거나 다시 만들지 않는다. 이 무결성 검사는 로컬·원격 검색 순위나 답변 품질 비교 결과를 뜻하지 않는다. Qdrant Cloud에 연결할 수 없거나 검증에 실패하면 자동으로 `numpy`에 fallback하지 않는다. 이전 로컬 경로가 필요하면 `table_rag.vector_backend: numpy`를 지정한다. [빠른 시작](quickstart.md#2-sqlite와-검색-자료-준비)

<a id="qdrant-색인"></a>

## 기존 Basic Qdrant 색인

이 절은 기존 Basic 검색용 색인과 측정 절차다. Advanced TableRAG의 Qdrant Cloud 벡터 검색은 위 절을 따른다. 이 Basic 색인의 검색 전용 측정은 Qdrant Cloud 컬렉션을 쓴다. 서비스 자원 로더는 메모리 Qdrant만 쓰므로 Basic Cloud 컬렉션을 직접 읽지 않는다. 기존 평가 어댑터에서 이 색인으로 검색하려면 같은 실행에서 메모리 Qdrant에 적재해 넘긴다([로컬 B·C 평가 안내](local-bc.md#bc-평가-cli와-공통-bootstrap)). Basic 색인도 개인 YAML의 Cloud 주소와 `.env` 인증값을 사용한다.

[개발 환경](development.md#설치)을 준비하고 개인 YAML에 Cloud 주소를 설정하고 `.env`에 `QDRANT_API_KEY`를 넣는다. 기존 `artifacts/basic/index_manifest.json`과 팀 공용 컬렉션이 있으면 먼저 확인한다. 새 색인이 필요할 때 저장소 루트에서 실행한다. 두 명령의 데이터·모델 인자는 자체 기본값(`data/basic`, E5-small)을 사용한다.

```bash
python -m app.indexing.scripts.build_index
```

첫 실행은 E5 모델을 내려받는다. 기본 컬렉션은 `basic_multilingual-e5-small_p800`이다. 같은 이름이 있으면 기본적으로 중단하며, 공용 컬렉션을 삭제·재생성하는 `--recreate`는 팀과 합의한 경우에만 사용한다. 팀 비교에는 같은 컬렉션과 색인 설정을 사용한다.

| 생성물 | 용도 |
| --- | --- |
| `artifacts/basic/pages.jsonl`, `chunks.jsonl` | 페이지별 원문과 청크 |
| `artifacts/basic/ingestion_report.json` | PDF·쪽수·청크 수, 빈 페이지, 실패 |
| `artifacts/basic/index_manifest.json` | 모델·차원·청크 파일 해시·컬렉션 정보 |
| Qdrant 컬렉션 | 청크 임베딩과 메타데이터 |

## 검색만 확인

```bash
python -m app.evaluation.scripts.eval_retrieval --mode naive
python -m app.evaluation.scripts.eval_retrieval --mode hybrid
python -m app.evaluation.scripts.eval_retrieval --mode hybrid_rerank
```

`--split additional`로 질문 묶음을 바꾸고, `--k 8`로 평가할 상위 청크 수를 바꾼다. 결과는 `artifacts/basic/retrieval_<split>_<mode>.jsonl`에 남는다. 공식 답변 평가는 [평가 가이드](c-module-guide.md)를 따른다.

기존 Basic 색인의 검색 전용 측정은 두 질문 묶음 각 28문항을 채점했다. 2026-11-06 CPU 측정값이며 검색 시간에 Qdrant 왕복이 포함된다.

| 방식 | Recall@5 | 근거쪽@5 | 검색 시간 중앙값 |
| --- | ---: | ---: | ---: |
| Dense | 0.603 | 0.322 | 약 0.14초 |
| Hybrid | 0.681 | 0.417 | 약 0.15초 |
| Hybrid + Reranking | 0.709 | 0.550 | 약 6.7초 |

근거쪽@k는 근거 그룹별로 해당 문서·페이지가 상위 k개에 들어온 비율이다.
