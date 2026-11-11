# HG+IS 혼합 요청의 검색·선택 흐름 추적

2026-11-10 · 기존 `run-01` 후처리 · 새 검색/API/모델 추론 없음

`full`은 현재 개인 기본 실행의 결정론적 HG 원문 조회 경로이며, 앞선 holdout의 Luna/SQLite `baseline` arm과 다른 경로다([구분 기록](mixed-source-audit.md), [실행 코드](../../advanced_identifier_diagnostic.py)). `planned_is`와 비교해 IS 요청 근거만 추적했다. 여기서 4/6은 원문 규정 절로 검색한 결과 중 IS 요청만 채점한 값이다. HG+IS를 합친 최종 문맥의 통합 성능 개선으로 해석하지 않는다. 집계와 문항별 세부 rank는 [비공개 진단 JSON](../../../../artifacts/u_10o0o/advanced-identifier-diagnostic-20261110/mixed-mechanism.json)에 있다.

## 저장 후보의 RRF 전체 rank 재구성

`hybrid.weighted_rrf()`와 같은 k=60, 5:5 가중치로 보관된 BM25·Dense 각 Top20의 합집합(최대 40쪽)을 다시 정렬했다. 30개 arm의 재구성 Top20은 저장된 `pool`과 **30/30** 일치했다. 표의 B/D는 전체 코퍼스 순위, RRF는 Top20+Top20 합집합 순위다. `—`는 두 retriever의 저장 Top20 어디에도 없어 union에 없다는 뜻이다. RRF rank 21–40은 산술 재구성뿐이며 KURE에 보내거나 재검색하지 않았다. [저장 trace](../../../../artifacts/u_10o0o/advanced-identifier-diagnostic-20261110/run-01/traces.jsonl) · [구현](../../hybrid.py)

| 문항 | Full mixed B/D → RRF | Literal IS B/D → RRF | 저장 IS 계획 B/D → RRF |
| --- | --- | --- | --- |
| H-C07 | 176/2918 → — | 22/18 → 34 | 144/31 → — |
| H-C08 | 4/1738 → 8 | 1/1 → 1 | 1/1 → 1 |
| H-C09 | 135/2832 → — | 120/17 → 31 | 163/14 → 25 |
| H-C10 | 1/1165 → 1 | 4/1 → 2 | 2/1 → 1 |
| H-C11 | 15/3487 → 30 | 5/28 → 10 | 9/47 → 18 |
| H-C12 | 26/3033 → — | 305/3 → 7 | 777/17 → 34 |

Full mixed 검색은 IS 최종 근거 **1/6**, literal IS 단독은 **4/6**, 저장 계획 단독은 **3/6**이었다. Full 질문에 IS 페이지 필터를 적용해도 **1/6**이었고, literal IS 결과와 IS 페이지 필터 결과는 여섯 trace가 모두 동일했다. 따라서 literal C07/C09와 full C11의 gold page는 KURE가 보기 전 RRF Top20 cutoff에서 빠진다. 후보 한도 확대가 회수 가능성을 열 수는 있지만, 아직 rerank·최종 문맥 결과를 측정하지 않았다.

## KURE 512 입력 확인

KURE-Reranker-nano의 고정 revision을 `AutoTokenizer`로 로컬 로드하고, 실제 `(query, indexed page text)` 순서로 `truncation=True, max_length=512` 인코딩을 계산했다. full mixed 6쌍과 literal IS 6쌍 모두 지정 gold quote의 비공백 문자 span이 입력에 전부 보존됐다. [토큰 가시성 수치](../../../../artifacts/u_10o0o/advanced-identifier-diagnostic-20261110/mixed-mechanism.json)

이 결과가 실제 KURE scoring을 뜻하지는 않는다. Full mixed에서 gold IS 쪽이 후보 풀에 실제 있었던 문항은 H-C08·H-C10뿐이다. 나머지 네 문항의 quote 가시성은 해당 페이지가 KURE 입력에 들어간다고 가정한 사후 토크나이저 검사다. 모델 reranking은 추가 실행하지 않았다.

## H-C10 최종 패킹

Full mixed H-C10의 AS053 p.1은 BM25 1위, RRF 1위, KURE 4위, 리랭킹 후 4위였다. HG 출처 다섯 쪽이 앞에 추가되면서 확장 순서 9위가 됐고, 최종 8쪽 문맥에서 제외됐다. 최종 문맥은 **4,036/8,000 토큰**, `token_excluded_chunk_ids=0`, 정확히 8쪽이다. 따라서 이 누락은 8,000토큰 한도가 아니라 8쪽 한도에서 생겼다. [고정 문맥 목록](../../../../artifacts/u_10o0o/advanced-identifier-diagnostic-20261110/run-01/contexts.jsonl)

기준선은 유지한다. 이 진단은 여섯 IS 하위 요청의 검색·문맥 선택의 누락 원인을 좁히며, exact-page primary 기준 외의 코퍼스 전체 대체 근거를 조사하거나 통합 HG+IS 답변을 평가하지 않았다.
