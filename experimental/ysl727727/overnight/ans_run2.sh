#!/usr/bin/env bash
# shortcut: 제 PC 경로(파이썬 가상환경·데이터 폴더)를 고정, 다른 PC에서는 PY·EV·cd 경로만 바꾸기
# 2차 답변 평가: ans_run2.sh <level> <설정> <태그> <세트들(공백)>  (출처 ID 보정 켬, 결과 ov2-<lv>-<set>-<tag>)
LV=$1; CFG=$2; TAG=$3; shift 3
cd /c/Users/ysl72/Desktop/ADEN-hybrid
export PYTHONIOENCODING=utf-8 PYTHONPATH=experimental/ysl727727/overnight OV_LEVEL=$LV OV_K=8 OV_BUDGET=8000 OV_FIXCITE=1
PY=/c/Users/ysl72/venvs/aden-gpu/Scripts/python.exe
O=experimental/ysl727727/overnight
case $LV in
  basic) EV=data/basic/evaluation ;;
  inter) EV=/c/Users/ysl72/Desktop/123/inter/Intermediate_shipping_part01of04/evaluation ;;
  adv) EV=/c/Users/ysl72/Desktop/123/advanced/Advanced_shipping_part01of09/evaluation ;;
esac
for SET in "$@"; do
  case $SET in
    fp) Q=$O/data/false_premise_${LV}_questions.json; R=$O/data/false_premise_${LV}.json; IDS="" ;;
    synth|synth2) Q=$O/data/synth_${LV}_questions.json; R=$O/data/synth_${LV}.json
           IDS=$($PY -c "import json;print(','.join(json.load(open('$O/data/answer_sample_ids.json'))['${LV}_${SET}']))") ;;
    *) Q=$EV/${SET}_questions.json; R=$EV/${SET}.json
       IDS=$($PY -c "import json;print(','.join(json.load(open('$O/data/answer_sample_ids.json'))['${LV}_${SET}']))") ;;
  esac
  EXP=ov2-$LV-$SET-$TAG
  echo "=== $EXP 시작 $(date +%H:%M:%S)"
  OV_CFG=$CFG $PY -m app.evaluation.scripts.run_benchmark --questions $Q --references $R --adapter glue2:adapter \
    --modes hybrid_rerank ${IDS:+--ids $IDS} --k 8 --experiment-id $EXP --dataset-id $LV --config-hash $EXP \
    --artifacts-dir artifacts/overnight/answers --reports-dir artifacts/overnight/reports --judge-model gpt-6-luna --overwrite 2>&1 \
    | grep -E "Traceback|Error|rror:"
  echo "=== $EXP 끝 $(date +%H:%M:%S)"
done
