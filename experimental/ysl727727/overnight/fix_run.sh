#!/usr/bin/env bash
# shortcut: 제 PC 경로(파이썬 가상환경·데이터 폴더)를 고정, 다른 PC에서는 PY·EV·cd 경로만 바꾸기
# 출처 ID 형식 오류(ValueError)로 실패한 문항만 OV_FIXCITE=1로 다시 실행 → ov-<lv>-<set>-<tag>-fix
LV=$1; FINAL=$2
cd /c/Users/ysl72/Desktop/ADEN-hybrid
export PYTHONIOENCODING=utf-8 PYTHONPATH=experimental/ysl727727/overnight OV_LEVEL=$LV OV_K=8 OV_BUDGET=8000 OV_FIXCITE=1
PY=/c/Users/ysl72/venvs/aden-gpu/Scripts/python.exe
O=experimental/ysl727727/overnight
case $LV in
  basic) EV=data/basic/evaluation ;;
  inter) EV=/c/Users/ysl72/Desktop/123/inter/Intermediate_shipping_part01of04/evaluation ;;
  adv) EV=/c/Users/ysl72/Desktop/123/advanced/Advanced_shipping_part01of09/evaluation ;;
esac
for TAG in base final; do
  CFG=$([ $TAG = base ] && echo base || echo $FINAL)
  for SET in fp development additional; do
    RUNS=artifacts/overnight/answers/ov-$LV-$SET-$TAG/runs.jsonl
    IDS=$($PY -c "import json;print(','.join(json.loads(l)['question_id'] for l in open('$RUNS',encoding='utf-8') if json.loads(l)['status']=='error'))")
    [ -z "$IDS" ] && continue
    if [ $SET = fp ]; then Q=$O/data/false_premise_${LV}_questions.json; R=$O/data/false_premise_${LV}.json;
    else Q=$EV/${SET}_questions.json; R=$EV/${SET}.json; fi
    EXP=ov-$LV-$SET-$TAG-fix
    echo "=== $EXP ($IDS) 시작 $(date +%H:%M:%S)"
    OV_CFG=$CFG $PY -m app.evaluation.scripts.run_benchmark --questions $Q --references $R --adapter glue2:adapter \
      --modes hybrid_rerank --ids $IDS --k 8 --experiment-id $EXP --dataset-id $LV --config-hash $EXP \
      --artifacts-dir artifacts/overnight/answers --reports-dir artifacts/overnight/reports --judge-model gpt-6-luna --overwrite 2>&1 \
      | grep -E "glue2|Traceback|Error|rror:"
    echo "=== $EXP 끝 $(date +%H:%M:%S)"
  done
done
