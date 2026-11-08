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

색인·정제 입력은 해당 난이도의 `documents/*.pdf`다. 원본 PDF·평가 자료·[정제본](../design/knowledge-data.md)·추출 캐시·색인·전체 실행 기록은 로컬에서 관리한다. 보안 시험용 문구는 데이터로 처리한다.

## Qdrant 색인

[개발 환경](development.md#설치)을 준비하고 로컬 `.env`에 `QDRANT_URL`과 `QDRANT_API_KEY`를 설정한다. 기존 `artifacts/basic/index_manifest.json`과 팀 공용 컬렉션이 있으면 먼저 확인한다. 새 색인이 필요할 때 저장소 루트에서 실행한다.

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
