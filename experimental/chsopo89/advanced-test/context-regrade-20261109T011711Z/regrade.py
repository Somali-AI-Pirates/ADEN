"""Fixed 12-answer pilot; writes separate results and never regenerates answers."""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import sys

ROOT = Path('C:/Users/chsop/Downloads/ADEN')
sys.path.insert(0, str(ROOT))
os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
from dotenv import load_dotenv
from app.evaluation.graders import build_judge_prompt, grade_record, grade_faithfulness, make_ragas_faithfulness, load_prompt

load_dotenv(ROOT/'.env', override=False)
MODEL = 'gpt-6-luna'
BASE = ROOT/'artifacts/experiments/local-rules2-adv-r1'
REPORT = ROOT/'artifacts/local-reports/local-rules2-adv-r1'
KEYS = [(q, mode) for q in ['S07', 'S09', 'M01', 'M19'] for mode in ['naive', 'hybrid', 'hybrid_rerank']]


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def append_json(path, value):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False)+'\n')


def key(row):
    return row['question_id'], row['retrieval_mode']


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def judge_phase():
    from openai import OpenAI
    records = {key(row): row for row in read_rows(BASE/'runs.jsonl')}
    before = {key(row): row for row in read_rows(BASE/'scores.jsonl')}
    references = {row['id']: row for row in json.loads((ROOT/'data/advanced/evaluation/custom.json').read_text(encoding='utf-8-sig'))}
    selected = [records[k] for k in KEYS]
    template = load_prompt('judge.txt')
    old_manifest = json.loads((BASE/'manifest.json').read_text(encoding='utf-8'))
    assert 'sha256:'+sha256(template.encode()).hexdigest() == old_manifest['benchmark']['judge_prompt']
    assert len(selected) == 12 and all(r['dataset_id'] == 'advanced' and r['status'] != 'error' for r in selected)
    assert all(build_judge_prompt('{{final_context}}', r, {}) == r['model_context_text'] for r in selected)
    for r in selected:
        assert r['request_id'] == before[key(r)]['request_id']
        assert references[r['question_id']]['question'] == r['question']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = ROOT/'experimental/chsopo89/advanced-test'/f'context-regrade-{stamp}'
    target.mkdir(parents=True, exist_ok=False)
    manifest = dict(experiment_id=target.name, dataset_id='advanced', status='running',
                    kind='stored-answer-regrading-pilot', created_utc=stamp,
                    model=MODEL, judge_call_limit=12, generation_calls=0,
                    reference_policy='Current reference file unchanged during pilot; historical reference hash not recorded.',
                    keys=KEYS, sdk_max_retries=0, sdk_timeout_seconds=60,
                    sha256={str(p.relative_to(ROOT)): digest(p) for p in [BASE/'runs.jsonl',BASE/'scores.jsonl',ROOT/'data/advanced/evaluation/custom.json',ROOT/'app/evaluation/graders.py',ROOT/'app/evaluation/prompts/judge.txt']})
    write_json(target/'manifest.json', manifest)
    write_json(target/'references.json', references)
    for r in selected:
        append_json(target/'runs.jsonl', r)
        append_json(target/'before_scores.jsonl', before[key(r)])
    for path in sorted(REPORT.glob('faithfulness_gpt-6-luna_*.jsonl')):
        for row in read_rows(path):
            if key(row) in KEYS:
                append_json(target/'before_faithfulness.jsonl', row)
    client = OpenAI(max_retries=0, timeout=60)
    calls = 0

    def judge(prompt):
        nonlocal calls
        assert calls < 12
        calls += 1
        append_json(target/'judge_prompts.jsonl', dict(call=calls, prompt=prompt))
        response = client.chat.completions.create(model=MODEL, messages=[{'role':'user','content':prompt}], response_format={'type':'json_object'})
        usage = response.usage
        out = dict(text=response.choices[0].message.content, input_tokens=getattr(usage,'prompt_tokens',None), output_tokens=getattr(usage,'completion_tokens',None))
        append_json(target/'judge_responses.jsonl', dict(call=calls, **out))
        return out

    try:
        for record in selected:
            score = grade_record(record, references[record['question_id']], k=8, judge_fn=judge, faithfulness_fn=None, judge_template=template)
            append_json(target/'after_scores.jsonl', score)
            print('Judge', record['question_id'],record['retrieval_mode'], {c:s['value'] for c,s in score['judge'].items()}, flush=True)
            if any(s['status']=='unavailable' for s in score['judge'].values()):
                manifest['status']='judge_stopped_on_failure'
                break
        else:
            manifest['status']='judge_complete_ragas_pending'
    finally:
        manifest['judge_calls']=calls
        write_json(target/'manifest.json', manifest)
        client.close()
        print('RESULT_DIR', target, flush=True)


def ragas_phase(target):
    import ragas.llms
    from importlib.metadata import version
    original = ragas.llms.llm_factory

    def factory(*args, **kwargs):
        kwargs.setdefault('max_tokens',8192)
        if 'client' in kwargs:
            kwargs['client'] = kwargs['client'].with_options(timeout=60, max_retries=0)
        return original(*args, **kwargs)

    ragas.llms.llm_factory=factory
    records = read_rows(target/'runs.jsonl')
    after={key(row):row for row in read_rows(target/'after_scores.jsonl')}
    before={key(row):row for row in read_rows(target/'before_scores.jsonl')}
    assert len(records)==12
    assert not (target/'after_faithfulness.jsonl').exists()
    fn=make_ragas_faithfulness(MODEL)
    count=0
    for record in records:
        # Keep the original claim gate so the faithfulness comparison changes only context.
        claims=before[key(record)].get('has_factual_claims')
        faith,usage=grade_faithfulness(record,claims,fn)
        row=dict(question_id=record['question_id'],retrieval_mode=record['retrieval_mode'],request_id=record['request_id'],has_factual_claims=claims,**faith,usage=usage)
        append_json(target/'after_faithfulness.jsonl',row)
        print('RAGAS',record['question_id'],record['retrieval_mode'],faith['value'],faith['status'], flush=True)
        count+=1
        if faith['status']=='unavailable':break
    manifest=json.loads((target/'manifest.json').read_text(encoding='utf-8'))
    manifest.update(status='complete' if count==12 else 'ragas_stopped_on_failure',ragas_version=version('ragas'),ragas_max_tokens=8192,ragas_records=count,ragas_claim_gate='original stored Judge has_factual_claims')
    write_json(target/'manifest.json',manifest)


if __name__=='__main__':
    if len(sys.argv)==1:
        judge_phase()
    else:
        ragas_phase(Path(sys.argv[1]).resolve())
