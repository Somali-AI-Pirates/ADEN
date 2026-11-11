# 카테고리별 회귀와 문항 변화

## 회귀 집계의 모든 범주

| 범주 | 채점/전체 | 기존 초기·가용·최종 | IS 초기·가용·최종 | 최종 차이 |
| --- | ---: | ---: | ---: | ---: |
| S01 | 20/20 | 20/20 / 20/20 / 20/20 | 20/20 / 20/20 / 20/20 | +0 |
| S02 | 20/20 | 10/20 / 20/20 / 20/20 | 10/20 / 20/20 / 20/20 | +0 |
| S03 | 20/20 | 20/20 / 20/20 / 20/20 | 20/20 / 20/20 / 20/20 | +0 |
| S04 | 20/20 | 10/20 / 20/20 / 20/20 | 10/20 / 20/20 / 20/20 | +0 |
| S05 | 20/20 | 20/20 / 20/20 / 20/20 | 20/20 / 20/20 / 20/20 | +0 |
| S06 | 20/20 | 0/20 / 20/20 / 20/20 | 0/20 / 20/20 / 20/20 | +0 |
| S07 | 20/20 | 20/20 / 20/20 / 20/20 | 20/20 / 20/20 / 20/20 | +0 |
| categories_all | 200/220 | 132/200 / 172/200 / 170/200 | 134/200 / 174/200 / 172/200 | +2 |
| categories_positive | 200/200 | 132/200 / 172/200 / 170/200 | 134/200 / 174/200 / 172/200 | +2 |
| negative_control/near_domain | 0/10 | —/0 / —/0 / —/0 | —/0 / —/0 / —/0 | +0 |
| negative_control/unrelated | 0/10 | —/0 / —/0 / —/0 | —/0 / —/0 / —/0 | +0 |
| official_additional | 29/30 | 18/29 / 18/29 / 14/29 | 19/29 / 19/29 / 14/29 | +0 |
| official_all | 58/60 | 39/58 / 39/58 / 32/58 | 42/58 / 42/58 / 33/58 | +1 |
| official_complex_groups | 51/51 | 35/51 / 35/51 / 28/51 | 37/51 / 37/51 / 28/51 | +0 |
| official_development | 29/30 | 21/29 / 21/29 / 18/29 | 23/29 / 23/29 / 19/29 | +1 |
| official_multi_page | 51/51 | 35/51 / 35/51 / 28/51 | 37/51 / 37/51 / 28/51 | +0 |
| semantic_all | 40/40 | 14/40 / 14/40 / 13/40 | 15/40 / 15/40 / 14/40 | +1 |
| semantic_queries/cross_document_rule | 10/10 | 1/10 / 1/10 / 1/10 | 1/10 / 1/10 / 1/10 | +0 |
| semantic_queries/cross_document_workflow | 10/10 | 2/10 / 2/10 / 2/10 | 2/10 / 2/10 / 2/10 | +0 |
| semantic_queries/procedure_paraphrase | 10/10 | 6/10 / 6/10 / 6/10 | 6/10 / 6/10 / 6/10 | +0 |
| semantic_queries/rule_paraphrase | 10/10 | 5/10 / 5/10 / 4/10 | 6/10 / 6/10 / 5/10 | +1 |
| work_rules/applicability | 10/10 | 9/10 / 9/10 / 8/10 | 9/10 / 9/10 / 8/10 | +0 |
| work_rules/procedure | 10/10 | 9/10 / 9/10 / 9/10 | 10/10 / 10/10 / 10/10 | +1 |
| work_rules_all | 20/20 | 18/20 / 18/20 / 17/20 | 19/20 / 19/20 / 18/20 | +1 |

## 계획이 있는 의미 질의 40개: 개선 3개, 회귀 1개

| ID | 범주 | 변화 | 초기 | 가용 | 최종 |
| --- | --- | --- | ---: | ---: | ---: |
| semantic_queries:AS-SEM-01 | semantic_queries/procedure_paraphrase | 개선 | 0 → 1 | 0 → 1 | 0 → 1 |
| semantic_queries:AS-SEM-13 | semantic_queries/rule_paraphrase | 개선 | 1 → 1 | 1 → 1 | 0 → 1 |
| semantic_queries:AS-SEM-14 | semantic_queries/rule_paraphrase | 회귀 | 1 → 0 | 1 → 0 | 1 → 0 |
| semantic_queries:AS-SEM-21 | semantic_queries/cross_document_workflow | 개선 | 0 → 1 | 0 → 1 | 0 → 1 |

## 원 질문 회귀 280개: 개선 5개, 회귀 2개

| ID | 범주 | 변화 | 초기 | 가용 | 최종 |
| --- | --- | --- | ---: | ---: | ---: |
| official_development:AS-DEV03 | official_development | 개선 | 0 → 1 | 0 → 1 | 0 → 1 |
| work_rules:AS-WR-01 | work_rules/procedure | 개선 | 0 → 1 | 0 → 1 | 0 → 1 |
| semantic_queries:AS-SEM-06 | semantic_queries/procedure_paraphrase | 개선 | 0 → 1 | 0 → 1 | 0 → 1 |
| semantic_queries:AS-SEM-09 | semantic_queries/procedure_paraphrase | 회귀 | 1 → 0 | 1 → 0 | 1 → 0 |
| semantic_queries:AS-SEM-15 | semantic_queries/rule_paraphrase | 개선 | 0 → 1 | 0 → 1 | 0 → 1 |
| semantic_queries:AS-SEM-16 | semantic_queries/rule_paraphrase | 개선 | 0 → 1 | 0 → 1 | 0 → 1 |
| semantic_queries:AS-SEM-17 | semantic_queries/rule_paraphrase | 회귀 | 1 → 0 | 1 → 0 | 1 → 0 |

## 후보20이 바뀐 문항 (최종 상태와 별도)

| 실험 | ID | 범주 | 후보20 기존 → IS | 최종8 기존 → IS | 최종 상태 |
| --- | --- | --- | ---: | ---: | --- |
| semantic_planned | semantic_queries:AS-SEM-01 | semantic_queries/procedure_paraphrase | 0 → 1 | 0 → 1 | 개선 |
| semantic_planned | semantic_queries:AS-SEM-14 | semantic_queries/rule_paraphrase | 1 → 0 | 1 → 0 | 회귀 |
| semantic_planned | semantic_queries:AS-SEM-21 | semantic_queries/cross_document_workflow | 0 → 1 | 0 → 1 | 개선 |
| semantic_planned | semantic_queries:AS-SEM-24 | semantic_queries/cross_document_workflow | 1 → 0 | 0 → 0 | 동일 |
| regression | official_development:AS-DEV03 | official_development | 0 → 1 | 0 → 1 | 개선 |
| regression | official_development:AS-DEV04 | official_development | 0 → 1 | 0 → 0 | 동일 |
| regression | official_additional:AS-ADD01 | official_additional | 0 → 1 | 0 → 0 | 동일 |
| regression | work_rules:AS-WR-01 | work_rules/procedure | 0 → 1 | 0 → 1 | 개선 |
| regression | semantic_queries:AS-SEM-06 | semantic_queries/procedure_paraphrase | 0 → 1 | 0 → 1 | 개선 |
| regression | semantic_queries:AS-SEM-09 | semantic_queries/procedure_paraphrase | 1 → 0 | 1 → 0 | 회귀 |
| regression | semantic_queries:AS-SEM-15 | semantic_queries/rule_paraphrase | 0 → 1 | 0 → 1 | 개선 |
| regression | semantic_queries:AS-SEM-16 | semantic_queries/rule_paraphrase | 0 → 1 | 0 → 1 | 개선 |
| regression | semantic_queries:AS-SEM-17 | semantic_queries/rule_paraphrase | 1 → 0 | 1 → 0 | 회귀 |
