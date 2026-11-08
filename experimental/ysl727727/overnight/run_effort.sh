#!/usr/bin/env bash
# shortcut: 제 PC 경로(파이썬 가상환경·데이터 폴더)를 고정, 다른 PC에서는 PY·EV·cd 경로만 바꾸기
# 지연 줄이기 후보 평가 (공식+거짓 전제+2문서 합성, 결과 이름 끝 #all).
# 1) 추론 강도 none/low  2) 단순 질문 건너뛰기  4) 로컬 모델 질문 분해. 비교 기준 = 지금 v2.
set -e
cd "$(dirname "$0")"
PY=/c/Users/ysl72/venvs/aden-gpu/Scripts/python.exe
export PYTHONIOENCODING=utf-8
G=base+graph6+gfix+gfloor0.05+mmr0.2
FREE=$G+mq_decomp_none,$G+mq_decomp_low,$G+mq_decomp_qwen4b,$G+mq_decomp+sel30+skip1,$G+mq_decomp+sel30+skip2
PAID=$G+mq_decomp_none+sel30+seff_none,$G+mq_decomp_low+sel30+seff_low,$G+mq_decomp_qwen4b+sel30
$PY -X utf8 run.py basic,inter,adv $FREE all
$PY -X utf8 run.py basic,inter,adv $PAID all
echo RUN_EFFORT_DONE
