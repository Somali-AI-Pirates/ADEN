"""Contract checks for the reusable production release workflow."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow(path: str) -> dict:
    return yaml.load(
        (ROOT / path).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )


def test_ci_passes_only_the_named_deployment_key():
    ci = _workflow(".github/workflows/ci.yml")

    assert ci["jobs"]["release"]["secrets"] == {
        "ADEN_DEPLOY_SSH_KEY": "${{ secrets.ADEN_DEPLOY_SSH_KEY }}"
    }


def test_release_requires_key_and_keeps_production_gate():
    release = _workflow(".github/workflows/release.yml")

    assert (
        release["on"]["workflow_call"]["secrets"]["ADEN_DEPLOY_SSH_KEY"][
            "required"
        ]
        == "true"
    )

    deploy = release["jobs"]["deploy"]
    assert deploy["environment"] == "production"
    steps = {step["name"]: step for step in deploy["steps"]}
    assert steps["Record disabled deployment"]["if"] == (
        "vars.ADEN_DEPLOY_ENABLED != 'true'"
    )
    apply = steps["Apply the verified release"]
    assert apply["if"] == "vars.ADEN_DEPLOY_ENABLED == 'true'"
    assert apply["env"]["ADEN_SSH_KEY"] == "${{ secrets.ADEN_DEPLOY_SSH_KEY }}"

    script = apply["run"]
    assert 'printf \'%s\\n\' "$ADEN_SSH_KEY" > "$scratch/key"' in script
    assert "unset ADEN_SSH_KEY" in script
    assert 'echo "$ADEN_SSH_KEY"' not in script
    assert 'printf "$ADEN_SSH_KEY"' not in script
