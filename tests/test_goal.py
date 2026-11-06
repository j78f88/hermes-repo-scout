import json

from click.testing import CliRunner

from repo_scout.cli import main
from repo_scout.planner import compile_plan


def test_goal_to_plan_quickstart(fixture_repo):
    plan = compile_plan("Validate whether the README quickstart is real", str(fixture_repo), method="auto")
    assert plan.schema_version == "repo-scout.plan.v1"
    assert plan.normalized_goal is not None
    assert plan.normalized_goal.intent == "quickstart_reproducibility"
    assert "runnable_surface" in plan.lanes
    assert plan.runner_policy.runner == "no-exec"
    assert plan.runner_policy.execution_allowed is False


def test_goal_dry_run_plan_json(fixture_repo):
    result = CliRunner().invoke(main, ["goal", "Can this repo improve our agent orchestration stack?", str(fixture_repo), "--dry-run-plan"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["schema_version"] == "repo-scout.plan.v1"
    assert data["normalized_goal"]["intent"] == "adoption_fit"
    assert data["runner_policy"]["execution_allowed"] is False
