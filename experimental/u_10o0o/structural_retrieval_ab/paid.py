"""Recorded single Responses API requests, with no automatic retries."""

import json
import time
from .common import OUT, read, write, digest


async def ask(client, name, prompt, payload, schema, *, max_tokens=7000):
    path = OUT / "api" / (name + ".json")
    fingerprint = digest(
        {"prompt": prompt, "payload": payload, "schema": schema.model_json_schema()}
    )
    if path.exists():
        row = read(path)
        if row["fingerprint"] != fingerprint:
            raise RuntimeError("API input changed: " + name)
        if row["status"] != "complete":
            raise RuntimeError("Inspect recorded failed API request: " + name)
        return schema.model_validate(row["parsed"]), row
    marker = path.with_suffix(".started.json")
    if marker.exists():
        raise RuntimeError("Interrupted paid request requires inspection: " + name)
    write(marker, {"fingerprint": fingerprint})
    started = time.perf_counter()
    row = {
        "fingerprint": fingerprint,
        "prompt": prompt,
        "payload": payload,
        "schema": schema.model_json_schema(),
        "model": "gpt-6-luna",
        "reasoning": "medium",
    }
    try:
        response = await client.responses.create(
            model="gpt-6-luna",
            instructions=prompt,
            input=[
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
            ],
            tools=[
                {
                    "type": "function",
                    "name": "record_result",
                    "description": "Return the requested structured result.",
                    "strict": True,
                    "parameters": schema.model_json_schema(),
                }
            ],
            tool_choice={"type": "function", "name": "record_result"},
            parallel_tool_calls=False,
            reasoning={"effort": "medium"},
            max_output_tokens=max_tokens,
            store=False,
        )
        calls = [x for x in response.output if x.type == "function_call"]
        row["usage"] = response.usage.model_dump() if response.usage else {}
        row["response_id"] = response.id
        if response.status != "completed" or len(calls) != 1:
            raise ValueError("Incomplete structured response")
        parsed = schema.model_validate_json(calls[0].arguments)
        row.update(status="complete", parsed=parsed.model_dump())
    except Exception as exc:
        row.update(status="error", error_class=type(exc).__name__)
        write(path, {**row, "seconds": time.perf_counter() - started})
        raise
    row["seconds"] = time.perf_counter() - started
    write(path, row)
    return parsed, row
