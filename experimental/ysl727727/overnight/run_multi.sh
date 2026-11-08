#!/usr/bin/env bash
# shortcut: 제 PC 경로(파이썬 가상환경·데이터 폴더)를 고정, 다른 PC에서는 PY·EV·cd 경로만 바꾸기
# 다문서 합성(정답 문서 3·4개) 세트로 main 기준 단계 ①~⑤ 검색 평가. 결과 이름 끝 #multi.
set -e
cd "$(dirname "$0")"
PY=/c/Users/ysl72/venvs/aden-gpu/Scripts/python.exe
export PYTHONIOENCODING=utf-8
G=base+graph6+gfix+gfloor0.05+mmr0.2
NAMES=base,base+mmr0.2,$G,$G+mq_decomp,$G+mq_decomp+sel30
for lv in ${1:-basic inter adv}; do
  $PY qr.py $lv decomp multi
  $PY run.py $lv $NAMES multi
done
echo RUN_MULTI_DONE
