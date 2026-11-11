# 문항별 비교

모델 간 비교는 같은 조건에서 KURE-v2→Nemotron이다. 모델 내 비교는 Nemotron base20→변경 조건이다.

| 비교 | 문항 | 변화 | 후보 전→후 | 최종 전→후 | 실패 위치 전→후 |
|---|---|---|---|---|---|
| 모델간/base20 | official_development:AS-DEV20 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| 모델간/base20 | official_additional:AS-ADD05 | regressed | 1→0 | 1→0 | success→initial_retrieval_or_lookup |
| 모델간/base20 | official_additional:AS-ADD13 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| 모델간/base20 | official_additional:AS-ADD27 | improved | 1→1 | 0→1 | selection_or_budget→success |
| 모델간/base20 | semantic_queries:AS-SEM-14 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/base20 | semantic_queries:AS-SEM-18 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/base20 | semantic_queries:AS-SEM-25 | regressed | 1→0 | 1→0 | success→initial_retrieval_or_lookup |
| 모델간/base20 | semantic_queries:AS-SEM-26 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/base20 | semantic_queries:AS-SEM-32 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/base20 | semantic_queries:AS-SEM-40 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/union40 | official_development:AS-DEV18 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| 모델간/union40 | official_development:AS-DEV20 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| 모델간/union40 | official_additional:AS-ADD05 | regressed | 1→1 | 1→0 | success→initial_retrieval_or_lookup |
| 모델간/union40 | official_additional:AS-ADD13 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| 모델간/union40 | official_additional:AS-ADD27 | improved | 1→1 | 0→1 | selection_or_budget→success |
| 모델간/union40 | semantic_queries:AS-SEM-14 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/union40 | semantic_queries:AS-SEM-15 | improved | 1→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/union40 | semantic_queries:AS-SEM-18 | improved | 1→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/union40 | semantic_queries:AS-SEM-26 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/union40 | semantic_queries:AS-SEM-32 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/union40 | semantic_queries:AS-SEM-37 | regressed | 1→0 | 1→0 | success→initial_retrieval_or_lookup |
| 모델간/union40 | semantic_queries:AS-SEM-40 | improved | 1→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/scope20 | official_development:AS-DEV20 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| 모델간/scope20 | official_additional:AS-ADD05 | regressed | 1→0 | 1→0 | success→initial_retrieval_or_lookup |
| 모델간/scope20 | official_additional:AS-ADD27 | improved | 1→1 | 0→1 | selection_or_budget→success |
| 모델간/scope20 | semantic_queries:AS-SEM-14 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/scope20 | semantic_queries:AS-SEM-25 | regressed | 1→0 | 1→0 | success→initial_retrieval_or_lookup |
| 모델간/scope20 | semantic_queries:AS-SEM-26 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/scope20 | semantic_queries:AS-SEM-32 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/scope20 | semantic_queries:AS-SEM-37 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/scope40 | official_development:AS-DEV18 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| 모델간/scope40 | official_development:AS-DEV20 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| 모델간/scope40 | official_additional:AS-ADD05 | regressed | 1→1 | 1→0 | success→initial_retrieval_or_lookup |
| 모델간/scope40 | official_additional:AS-ADD27 | improved | 1→1 | 0→1 | selection_or_budget→success |
| 모델간/scope40 | semantic_queries:AS-SEM-14 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/scope40 | semantic_queries:AS-SEM-26 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| 모델간/scope40 | semantic_queries:AS-SEM-32 | improved | 1→1 | 0→1 | initial_retrieval_or_lookup→success |
| Nemotron내/scope20 | official_additional:AS-ADD13 | improved | 1→1 | 0→1 | selection_or_budget→success |
| Nemotron내/scope20 | semantic_queries:AS-SEM-20 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| Nemotron내/scope20 | semantic_queries:AS-SEM-37 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| Nemotron내/scope20 | semantic_queries:AS-SEM-40 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| Nemotron내/scope40 | official_development:AS-DEV18 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| Nemotron내/scope40 | official_additional:AS-ADD13 | improved | 1→1 | 0→1 | selection_or_budget→success |
| Nemotron내/scope40 | semantic_queries:AS-SEM-20 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| Nemotron내/scope40 | semantic_queries:AS-SEM-25 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| Nemotron내/scope40 | semantic_queries:AS-SEM-37 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| Nemotron내/scope40 | semantic_queries:AS-SEM-40 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| Nemotron내/union40 | official_development:AS-DEV18 | regressed | 1→1 | 1→0 | success→selection_or_budget |
| Nemotron내/union40 | semantic_queries:AS-SEM-25 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
