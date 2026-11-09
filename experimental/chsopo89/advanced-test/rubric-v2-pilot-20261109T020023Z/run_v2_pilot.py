"""Regrade the same 12 stored answers with the v2 rubric; no generation or retrieval."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import sys

ROOT=Path('C:/Users/chsop/Downloads/ADEN')
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from app.evaluation.dataset import load_references
from app.evaluation.graders import build_judge_prompt,grade_record

BASE=ROOT/'experimental/chsopo89/advanced-test/context-regrade-20261109T011711Z'
RUBRIC=ROOT/'experimental/chsopo89/advanced-rubric-v2'
MODEL='gpt-6-luna'


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line]


def key(row):
    return row['question_id'],row['retrieval_mode']


def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')


def append(path,value):
    with path.open('a',encoding='utf-8',newline='\n') as stream:stream.write(json.dumps(value,ensure_ascii=False)+'\n')


def preflight():
    records=rows(BASE/'runs.jsonl')
    before={key(row):row for row in rows(BASE/'after_scores.jsonl')}
    references=load_references(RUBRIC/'references_v2.json')
    template=(RUBRIC/'judge_advanced_v2.txt').read_text(encoding='utf-8')
    old_manifest=json.loads((BASE/'manifest.json').read_text(encoding='utf-8'))
    assert len(records)==len(before)==12
    assert {r['question_id'] for r in records}=={'S07','S09','M01','M19'}
    assert {r['retrieval_mode'] for r in records}=={'naive','hybrid','hybrid_rerank'}
    assert sha256((ROOT/'app/evaluation/graders.py').read_bytes()).hexdigest()==old_manifest['sha256']['app\\evaluation\\graders.py']
    for record in records:
        assert record['dataset_id']=='advanced' and record['status']!='error'
        assert record['request_id']==before[key(record)]['request_id']
        assert references[record['question_id']]['question']==record['question']
        assert sha256(record['model_context_text'].encode()).hexdigest()==record['model_context_hash']
        prompt=build_judge_prompt(template,record,references[record['question_id']])
        assert record['model_context_text'] in prompt and '{{reference}}' not in prompt
    return records,before,references,template


def main():
    records,before,references,template=preflight()
    if '--check-only' in sys.argv:
        print('Preflight passed: 12 matched stored answers; same grader/context; v2 criteria only; no API calls.')
        return
    from openai import OpenAI
    load_dotenv(ROOT/'.env',override=False)
    assert os.environ.get('OPENAI_API_KEY'), 'Configured Judge credentials required.'
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target=ROOT/'experimental/chsopo89/advanced-test'/f'rubric-v2-pilot-{stamp}'
    target.mkdir(parents=True,exist_ok=False)
    source_files=[BASE/'runs.jsonl',BASE/'after_scores.jsonl',BASE/'after_faithfulness.jsonl',RUBRIC/'references_v2.json',RUBRIC/'judge_advanced_v2.txt',ROOT/'app/evaluation/graders.py']
    manifest=dict(experiment_id=target.name,dataset_id='advanced',status='running',kind='v2-rubric-stored-answer-pilot',
                  created_utc=stamp,source_experiment_id=BASE.name,model=MODEL,judge_call_limit=12,
                  generation_calls=0,retrieval_calls=0,ragas_calls=0,ragas_policy='Not rerun; same answers and context; earlier scores are prior measurements only.',
                  changed=['reference rubric','dedicated Judge prompt'],fixed=['question','answer','reason','citations','request_id','model_context_text','Judge model','grader implementation'],
                  sdk_max_retries=0,sdk_timeout_seconds=60,
                  source_sha256={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in source_files})
    dump(target/'manifest.json',manifest)
    dump(target/'references_v2_used.json',[references[r['question_id']] for r in records[::3]])
    (target/'judge_v2_used.txt').write_text(template,encoding='utf-8',newline='\n')
    (target/'run_v2_pilot.py').write_bytes(Path(__file__).read_bytes())
    for record in records:
        append(target/'before_scores.jsonl',before[key(record)])
        append(target/'selected_answers.jsonl',{k:record[k] for k in ['question_id','retrieval_mode','request_id','question','answer','reason','citations','model_context_hash']})
    client=OpenAI(max_retries=0,timeout=60)
    calls=0

    def judge(prompt):
        nonlocal calls
        assert calls<12
        calls+=1
        append(target/'judge_prompts.jsonl',dict(call=calls,prompt=prompt))
        response=client.chat.completions.create(model=MODEL,messages=[{'role':'user','content':prompt}],response_format={'type':'json_object'})
        usage=response.usage
        out=dict(text=response.choices[0].message.content,input_tokens=getattr(usage,'prompt_tokens',None),output_tokens=getattr(usage,'completion_tokens',None))
        append(target/'judge_responses.jsonl',dict(call=calls,**out))
        return out

    try:
        for record in records:
            score=grade_record(record,references[record['question_id']],k=8,judge_fn=judge,faithfulness_fn=None,judge_template=template)
            score.update(experiment_id=target.name,source_experiment_id=BASE.name,rubric_version='advanced-v2.0-draft')
            append(target/'after_scores.jsonl',score)
            print(record['question_id'],record['retrieval_mode'],{c:v['value'] for c,v in score['judge'].items()},flush=True)
            if any(v['status']=='unavailable' for v in score['judge'].values()):
                manifest['status']='stopped_on_evaluation_failure'
                break
        else:manifest['status']='complete'
    finally:
        manifest['judge_calls']=calls
        dump(target/'manifest.json',manifest)
        client.close()
        print('RESULT_DIR',target,flush=True)


if __name__=='__main__':main()
