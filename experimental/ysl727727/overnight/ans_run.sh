#!/usr/bin/env bash
# shortcut: 제 PC 경로(파이썬 가상환경·데이터 폴더)를 고정, 다른 PC에서는 PY·EV·cd 경로만 바꾸기
# 답변 평가 (단계 하나): 기존(base) vs 최종 후보, 거짓 전제 20 + development 12 + additional 12 (무작위 표본, seed 7)
LV=$1; FINAL=$2
cd /c/Users/ysl72/Desktop/ADEN-hybrid
export PYTHONIOENCODING=utf-8 PYTHONPATH=experimental/ysl727727/overnight OV_LEVEL=$LV OV_K=8 OV_BUDGET=8000
PY=/c/Users/ysl72/venvs/aden-gpu/Scripts/python.exe
O=experimental/ysl727727/overnight
case $LV in
  basic) EV=data/basic/evaluation ;;
  inter) EV=/c/Users/ysl72/Desktop/123/inter/Intermediate_shipping_part01of04/evaluation ;;
  adv) EV=/c/Users/ysl72/Desktop/123/advanced/Advanced_shipping_part01of09/evaluation ;;
esac
for CFG in base $FINAL; do
  TAG=$([ "$CFG" = base ] && echo base || echo final)
  for SET in fp development additional; do
    if [ $SET = fp ]; then Q=$O/data/false_premise_${LV}_questions.json; R=$O/data/false_premise_${LV}.json; IDS="";
    else Q=$EV/${SET}_questions.json; R=$EV/${SET}.json; IDS=$($PY -c "import json;print(','.join(json.load(open('$O/data/answer_sample_ids.json'))['${LV}_${SET}']))"); fi
    EXP=ov-$LV-$SET-$TAG
    echo "=== $EXP 시작 $(date +%H:%M:%S)"
    OV_CFG=$CFG $PY -m app.evaluation.scripts.run_benchmark --questions $Q --references $R --adapter glue2:adapter \
      --modes hybrid_rerank ${IDS:+--ids $IDS} --k 8 --experiment-id $EXP --dataset-id $LV --config-hash $EXP \
      --artifacts-dir artifacts/overnight/answers --reports-dir artifacts/overnight/reports --judge-model gpt-6-luna --overwrite 2>&1 \
      | grep -E "glue2|실행 기록|Traceback|Error|rror:" 
    echo "=== $EXP 끝 $(date +%H:%M:%S)"
  done
done
