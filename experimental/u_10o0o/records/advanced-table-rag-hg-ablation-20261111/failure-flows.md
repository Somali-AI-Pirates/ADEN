# 문항별 변화와 검색 흐름

A는 HG ON, B는 HG OFF다. 후보 합집합은 각 검색이 반환한 최대20쪽들의 합집합이다. 검색기별50개 순위 전체를 조사했다는 뜻은 아니다. 아래 확보 여부는 기존 지정 근거 기준이고, 마지막 판정은 실제 문맥 검수를 반영했다.

| 문항 | 검수 ON→OFF | HG 순위 보강 | OFF 검색 합집합 충족 | OFF 최종후보20 충족 | OFF 최종8 지정 충족 | 계획호출 ON/OFF | SQL ON/OFF | 해석 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| official_development:AS-DEV09 | 1→0 | False | 1 | 1 | 0 | 3/2 | 0/0 | HG 보강 없음; 새 Luna 계획 변동 포함 |
| official_additional:AS-ADD09 | 0→1 | False | 1 | 1 | 1 | 3/5 | 0/0 | HG 보강 없음; 새 Luna 계획 변동 포함 |
| scenarios:AS-S02-17 | 1→0 | True | 1 | 1 | 0 | 1/2 | 0/0 | 최종8에서 누락 |
| scenarios:AS-S03-11 | 1→0 | True | 1 | 0 | 0 | 2/4 | 0/1 | 병합 후보20에서 누락 |
| scenarios:AS-S06-02 | 1→0 | True | 0 | 0 | 0 | 1/3 | 0/0 | 검색 후보 합집합의 근거 부족 |
| scenarios:AS-S06-08 | 1→0 | True | 0 | 0 | 0 | 1/2 | 0/1 | 검색 후보 합집합의 근거 부족 |
| scenarios:AS-S06-10 | 1→0 | True | 0 | 0 | 0 | 1/3 | 0/0 | 검색 후보 합집합의 근거 부족 |
| scenarios:AS-S06-11 | 1→0 | True | 0 | 0 | 0 | 1/2 | 0/0 | 검색 후보 합집합의 근거 부족 |
| scenarios:AS-S06-20 | 1→0 | True | 1 | 1 | 0 | 2/2 | 0/0 | 최종8에서 누락 |
| semantic_queries:AS-SEM-22 | 0→1 | False | 1 | 1 | 1 | 3/4 | 0/0 | HG 보강 없음; 새 Luna 계획 변동 포함 |
| semantic_queries:AS-SEM-23 | 1→0 | False | 0 | 0 | 0 | 3/4 | 0/0 | HG 보강 없음; 새 Luna 계획 변동 포함 |
| semantic_queries:AS-SEM-35 | 0→1 | False | 0 | 0 | 0 | 2/3 | 0/0 | HG 보강 없음; 새 Luna 계획 변동 포함 |

후보20이나 최종8에서 누락된 문항도 있어 모든 손실을 최초 검색 실패로 묶지 않는다. SQLite 실행 성공과 질문에 필요한 사건의 근거 확보도 구분한다. 정확한 출처 순서는 [흐름 JSON](failure-flows.json)에 보존했다.
