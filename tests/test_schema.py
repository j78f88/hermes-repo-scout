from repo_scout.intake import inventory_repo, parse_repo_ref
from repo_scout.schemas import ActionRegisterEntry, Claim, Evidence, Finding, Report, RepoSnapshot, ResearchPlan, Verdict, VerdictRecord


def test_schema_models_minimal():
    repo = RepoSnapshot(url="file:///tmp/demo", name="demo")
    verdict = VerdictRecord(decision=Verdict.INVESTIGATE, summary="Needs more evidence")
    report = Report(
        run_id="run_test",
        generated_by={"tool": "repo-scout", "version": "test"},
        repo=repo,
        summary="summary",
        verdict=verdict,
        action_register=[
            ActionRegisterEntry(
                action_id="action_test",
                action_type="rescout",
                title="Register demo recommendation",
                recommendation="investigate",
                why="Needs more evidence",
                next_registration_step="rescout",
            )
        ],
    )
    assert report.schema_version == "repo-scout.report.v1"
    assert report.verdict.decision == Verdict.INVESTIGATE
    assert Claim.__name__ == "Claim"
    assert Evidence.__name__ == "Evidence"
    assert Finding.__name__ == "Finding"
    assert ResearchPlan.__name__ == "ResearchPlan"


def test_parse_github_and_local_fixture(fixture_repo):
    local = parse_repo_ref(str(fixture_repo))
    assert local.is_local
    gh = parse_repo_ref("https://github.com/owner/repo.git")
    assert not gh.is_local
    assert gh.owner == "owner"
    assert gh.name == "repo"


def test_inventory_caps_and_ignores(fixture_repo):
    snap = inventory_repo(fixture_repo, max_files=10, max_bytes=100000)
    paths = {f["path"] for f in snap.files}
    assert "README.md" in paths
    assert "pyproject.toml" in paths
    assert snap.files_scanned >= 2
