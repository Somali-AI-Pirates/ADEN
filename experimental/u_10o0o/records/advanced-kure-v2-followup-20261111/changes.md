# 문항별 개선·하락

원래 정답 기준으로 base20과 비교한다. 나머지 문항은 전체 근거 충족 여부가 같다. 상세 후보·출처 순위와 원문 문맥은 로컬 artifacts에 보관한다.

| 조건 | 문항 | 변화 | 후보 충족 전→후 | 최종 충족 전→후 | 실패 위치 전→후 |
|---|---|---|---|---|---|
| scope20 | semantic_queries:AS-SEM-18 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| scope20 | semantic_queries:AS-SEM-20 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| scope40 | semantic_queries:AS-SEM-18 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| scope40 | semantic_queries:AS-SEM-20 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| scope40 | semantic_queries:AS-SEM-37 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
| union40 | semantic_queries:AS-SEM-15 | regressed | 1→1 | 1→0 | success→initial_retrieval_or_lookup |
| union40 | semantic_queries:AS-SEM-37 | improved | 0→1 | 0→1 | initial_retrieval_or_lookup→success |
