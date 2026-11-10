"""모의 모델로 검증 실패·재시도·생성 실패를 만들어 실제 Langfuse에 기록한다(모델 호출 비용 없음).

    python experimental/Damsebi/mock_retry_trace.py
    python experimental/Damsebi/check_langfuse.py runs artifacts/experiments/mock-retry-20261110/runs.jsonl
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.observability.records import normalize_run_record  # noqa: E402
from app.service.adapters import make_query_adapter  # noqa: E402
from app.service.bootstrap import load_settings_with_env  # noqa: E402
from app.service.resources import load_generation_resources  # noqa: E402
from experimental.Damsebi.bc_adapter import stored_provider  # noqa: E402
from tests.pipeline_fakes import FakeModel  # noqa: E402


class SequenceModel(FakeModel):
    """호출마다 정해진 출처 ID를 내거나 예외를 던진다."""

    def __init__(self, steps):
        super().__init__(["S1"])
        self.steps = list(steps)

    def invoke(self, messages, config=None, **kwargs):
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        self.source_ids = step
        return super().invoke(messages, config, **kwargs)


settings = load_settings_with_env(ROOT / "configs" / "basic.yaml")
source = ROOT / "artifacts/experiments/basic-smoke-s01-20261110/runs.jsonl"
question = json.loads(source.read_text(encoding="utf-8").splitlines()[0])["question"]
resources = load_generation_resources(settings, evidence_provider=stored_provider(source, settings))
adapter = make_query_adapter(resources, settings)
stamp = int(time.time())
scenarios = {
    "retry-then-ok": [["S99"], ["S1"]],          # 검증 실패 후 재시도 성공
    "validation-fails-twice": [["S99"], ["S98"]],  # 두 번 모두 검증 실패 → 오류
    "model-call-fails": [RuntimeError("mock model failure")],  # 생성 호출 실패 → 오류
}
out = ROOT / "artifacts/experiments/mock-retry-20261110"
out.mkdir(parents=True, exist_ok=True)
rows = []
for name, steps in scenarios.items():
    resources.model = SequenceModel(steps)
    record = dict(adapter(question, "naive", f"mock-{name}-{stamp}"))
    record.update(experiment_id="mock-retry-20261110", question_id="S01")
    record = normalize_run_record(record)
    rows.append(record)
    print(name, record["status"], record.get("error_type"), "| attempts",
          [(a["attempt"], a.get("error"), a.get("latency_ms")) for a in record["attempts"]],
          "| trace", record["langfuse_trace_id"], record["langfuse_status"], record.get("langfuse_errors"))
(out / "runs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
print("flush", resources.tracer.flush())
resources.close()
