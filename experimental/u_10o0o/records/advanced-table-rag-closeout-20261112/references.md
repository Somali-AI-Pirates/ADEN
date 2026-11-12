# 참고 논문·기술 문서와 실제 적용 범위

2026-11-12 종료 정리에서 출판사·저자·공식 문서를 다시 확인했다. 아래의 '연결'은 실험 설계와의 관계이며 논문 성능을 재현했다는 뜻이 아니다. 논문의 모델·데이터·학습·구현을 그대로 사용하지 않은 부분을 명시한다. 사용자 제공 조사문은 아이디어 출처로 활용했고, 그 문서의 외부 성능 수치를 ADEN 결과로 가져오지 않았다.

## 직접 참고한 TableRAG

| 자료 | 확인한 아이디어 | ADEN 적용·실험 | 남은 차이 |
|---|---|---|---|
| [TableRAG: A Retrieval Augmented Generation Framework for Heterogeneous Document Reasoning — Yu et al., EMNLP 2025](https://aclanthology.org/2025.emnlp-main.710/) · [논문 PDF](https://aclanthology.org/2025.emnlp-main.710.pdf) · [저자 코드](https://github.com/yxh-y/TableRAG) | 텍스트·표를 함께 다루고, 질문 분해·검색·SQL·중간 결과를 순차 연결 | IS 실제 표 본문 색인, 원본 표·스키마 연결, 조건부 하위 질문, 읽기 전용 SQL, 결과 피드백. 이후 단계 분리 workflow도 별도 비교 | 한국어 모델·SQLite·페이지 예산·공통 기록 조회는 프로젝트 선택. HeteQA나 논문 점수 재현 아님 |
| [TableRAG: Million-Token Table Understanding with Language Models — Chen et al., NeurIPS 2024](https://papers.nips.cc/paper_files/paper/2024/hash/88dd7aa6979e352fda7c4952ca8eac59-Abstract-Conference.html) | 전체 거대 표를 프롬프트에 넣기보다 관련 스키마·셀을 검색해 프로그램 연산을 돕는 접근 | 통합 경로의 표 목록·열·예시 발견과 관측 스키마에 제한한 SQL 설계에 참고 | 위 EMNLP2025 논문과 동명의 다른 연구. 백만 토큰 벤치나 해당 검색기 재현 아님 |

실제 요소별 대응은 [초기 논문 대응표](../advanced-table-rag-20261111/paper-coverage.md)와 [단계 분리의 논문/ADEN 구분](../advanced-table-rag-workflow-20261111/paper-alignment.md)에 있다. 최종 선택은 논문과 가장 닮은 호출 수를 만드는 것이 아니라 이 데이터에서 유지할 효과와 비용을 판단한 결과다.

## 사용자가 제공한 관계·근거 누락 조사 자료

아래는 처음 문제를 정의할 때 검토한 연구다. 이번 종료에서 서지·초록을 확인했으며 '배경'으로 표시한 항목의 고유 알고리즘이나 학습 모델을 새로 구현하지 않았다.

| 연구·1차 출처 | 핵심 문제 | 이 기록에서의 위치 |
|---|---|---|
| [PAR²-RAG: Planned Active Retrieval and Reasoning for Multi-Hop Question Answering — ACL Industry 2026](https://aclanthology.org/2026.acl-industry.118/) | 초기 후보 폭과 후속 집중 검색·충분성 제어를 분리 | 고정 계획이 초기에 잘못 확정되는 위험을 검토한 배경. 이번 bounded workflow가 PAR²의 정식 재현은 아님 |
| [SPARE-RAG: Evidence Repair and Verification-Guided Sequential Neural Pruning for Multi-Hop RAG — Information 2026](https://www.mdpi.com/2078-2489/17/10/1052) | 빠진 근거를 보충하고 근거 삭제가 남은 충족에 미치는 영향을 검사 | 근거 집합의 완전성과 관련성 순위를 구분하는 배경. 논문의 순차 neural pruning은 미구현 |
| [Sufficient Context: A New Lens on Retrieval Augmented Generation Systems — ICLR 2025](https://research.google/pubs/sufficient-context-a-new-lens-on-retrieval-augmented-generation-systems-2/) | 반환 문맥이 답에 충분한지와 생성 모델의 활용 실패를 분리 | 실제 반환 문맥·답변 정확도·모호성 종료를 별도로 기록하는 평가 관점 |
| [S2G-RAG: Structured Sufficiency and Gap Judging for Iterative Retrieval-Augmented QA — ACL 2026](https://aclanthology.org/2026.acl-long.1185/) | 부족한 근거를 구조화해 다음 검색에 연결 | 요청별 상태·보충 요구의 배경. 논문의 학습된 판단 모델은 사용하지 않음 |
| [Don't Lose Entities from Retrieval to Generation: Dual Entity Recovery RAG for multi-hop QA — Findings EMNLP 2026](https://aclanthology.org/2026.findings-emnlp.1068/) | 하위 질문·문장 분리에서 연결 대상이 사라지는 문제 | 대상별 코드·원문 출처 연결을 유지해야 한다는 배경. DER-RAG 모델/절차 재현 아님 |
| [Knowledge Graph-Guided Retrieval Augmented Generation — NAACL 2025](https://aclanthology.org/2025.naacl-long.449/) | 청크 간 사실 관계로 검색 다양성과 연결성을 보완 | 그래프 대안 검토. 최종 구현은 검증된 명시 코드·기록 관계의 SQLite 조회이며 KG²RAG나 범용 GraphRAG가 아님 |
| [Benchmarking Deep Search over Heterogeneous Enterprise Data — EMNLP Industry 2025](https://aclanthology.org/2025.emnlp-industry.34/) | 여러 형태의 업무 자료에서 출처를 연결하는 검색 평가 | 업무형 다문서 질문에서 최종 답만 보지 않고 필요한 근거와 호출을 추적하는 배경 |
| [Question Decomposition for Retrieval-Augmented Generation — ACL SRW 2025](https://aclanthology.org/2025.acl-srw.32/) | 한 번의 질의로 찾기 어려운 다중 근거를 하위 질문으로 검색 | 요청별 검색 대안의 배경. 이 논문의 지표를 ADEN의 개선 예상치로 사용하지 않음 |
| [Optimizing Question Semantic Space for Dynamic Retrieval-Augmented Multi-hop Question Answering (Q-DREAM) — ACL 2025](https://aclanthology.org/2025.acl-long.871/) | 의존적인 하위 질문의 의미 불일치와 검색 비용 | 질문 계획 대안으로 검토. 최종 경로에 Q-DREAM 학습/최적화 모듈 없음 |
| [Do RAG Systems Cover What Matters? Evaluating and Optimizing Responses with Sub-Question Coverage — NAACL 2025](https://aclanthology.org/2025.naacl-long.301/) | 핵심·배경·후속 질문별 응답 커버리지 | 질문이 요구하지 않은 설명을 필수 정답으로 확대하지 않는 평가 구분에 참고. 해당 자동 지표를 그대로 실행한 것은 아님 |
| [The Bottleneck Is Retrieval, Not Reading: Diagnosing Evidence-Based Generation with RAG for Long Context Question Answering — Findings EMNLP 2026](https://aclanthology.org/2026.findings-emnlp.2222/) | 필요한 근거의 검색 실패와 문맥 활용을 분리 | 후보 확보/본문 관측/최종 보존의 최초 누락을 따로 진단하는 배경 |

이 논문들의 성공 사례가 최종 ADEN 설정의 우위를 증명하지 않는다. 모델이 만든 요구 목록의 충족, 인용의 원문 존재, 실제 질문의 완전한 해결은 서로 다르다. 구조화된 상태를 출력한다는 이유만으로 의미 정확성을 보장하지 않는다.

## 실행 기술과 고정 모델

| 자료 | 사용한 부분 | 채택 근거의 범위 |
|---|---|---|
| [OpenAI Responses 함수 호출 공식 문서](https://developers.openai.com/api/docs/guides/function-calling) | strict 함수 스키마, function_call의 인자 검증, 실제 도구 실행과 call_id에 연결한 결과 반환 | 도구 인자 형식을 제한하는 계약. SQL/조회 의미가 맞다는 보장이 아니므로 Python 검증·출처·읽기 권한 검사 별도 |
| [KURE-v3 모델 카드](https://huggingface.co/nlpai-lab/KURE-v3) | 한국어 임베딩 자산의 모델 ID·사용 계약 | 논문 최고 점수만으로 채택하지 않고 이 레포의 저장된 모델 비교와 실행 지문 사용 |
| [KURE-Reranker-nano 모델 카드](https://huggingface.co/nlpai-lab/KURE-Reranker-nano) | 검색 후보 재순위화 모델 | 별도 학습 없이 고정 리비전 사용. 후보에 없는 페이지를 리랭커가 만들어낼 수 없음 |
| [SQLite authorizer](https://www.sqlite.org/c3ref/set_authorizer.html) · [query_only](https://www.sqlite.org/pragma.html#pragma_query_only) | 읽기 전용 실행과 허용 테이블/동작 제한 | 관측된 표만 조회·수정 금지. 값·단위·집계 모집단의 의미는 별도 검증 대상 |
| [기존 조사 모음](../../research/advanced-retrieval-foundations-20261110/README.md) | ID 정규화·키워드/벡터·명시 관계·구조화 검색의 선행 검토 | 이번 TableRAG 이전 배경 기록이며 새 검증 결과로 합산하지 않음 |

외부 자료의 절차와 프로젝트 추가 구현을 구분해 기록했다. 논문은 참고 근거이고 최종 OFF 선택은 [같은 조건의 공식 ON/OFF 관측과 운영 비용 판단](decision.md)에 근거한다.
