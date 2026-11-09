# chsopo89 실험 기록

- [Advanced 평가 결과](advanced_test.md)
- [Advanced 평가 기준 v2 초안](advanced-rubric-v2/README.md)

## 자료 공유와 업로드 범위

2026-11-09 사용자 전달: 팀원이 색인을 당일 공유할 예정이며 PDF 원본도 클라우드에 올릴 예정이다. 현재 공유 완료·접근 권한·색인 버전은 확인하지 않았다.

앞으로도 원본 PDF, 색인 데이터·스냅샷·로컬 벡터 저장소, 실제 `.env`·인증 파일은 Git 업로드에서 제외한다. 공유 자료는 팀의 클라우드·색인 저장 위치로 연결한다. 업로드 전 파일 목록과 인증값 포함 여부를 확인한다.

## 다음 Advanced 테스트

[GitHub #19: A 검색 개선 v2 실험](https://github.com/Somali-AI-Pirates/ADEN/pull/19)을 참고한다. 해당 설명은 개인 실험 코드이며 기본 서비스의 검색 단계가 자동 변경되는 것은 아니다.

- 코드와 요약: `experimental/ysl727727/overnight/REPORT_v2.md`
- 비교할 단계: 문서 몰림 완화, 같은 식별자 연결, 질문 분해, 근거 묶음 선택
- Naive·Hybrid·Hybrid+Rerank 기본 비교와 v2 실험 결과를 별도로 기록한다.
- 테스트 전 공유 색인·PDF의 위치와 버전, 모델·질문·k·문맥 예산·CPU/GPU 조건을 확인한다. 다른 장치의 응답 시간을 그대로 비교하지 않는다.
- 질문 분해·근거 선택은 추가 LLM 호출이 있으므로 실행 범위·비용을 정한 뒤 테스트한다.
- Git 작업은 사용자 요청에 따라 `feat/prompt-choi`에 올린다. main 병합은 별도 지시가 있을 때 진행한다.
