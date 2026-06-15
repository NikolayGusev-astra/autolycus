from pathlib import Path
import subprocess

from plugins.ontonet.diff_to_onto import build_plan, map_file_to_entity, repo_root_from


def test_map_file_to_entity_core_gateway_plugin():
    assert map_file_to_entity("run_agent.py") == ("run_agent", "[CORE] Модуль ядра", "CORE")
    assert map_file_to_entity("gateway/run.py") == ("GatewayRunner", "[GW] Модуль Gateway", "GW")
    assert map_file_to_entity("plugins/ontonet/diff_to_onto.py") == (
        "ontonet",
        "[PLGN] Плагин ontonet",
        "PLGN",
    )


def test_build_plan_marks_hook_plumbing_in_core_file(tmp_path: Path):
    repo = tmp_path / "repo"
    base = tmp_path / "base"
    base.mkdir()
    (base / "hermes_cli").mkdir()
    (base / "hermes_cli" / "plugins.py").write_text("old\n", encoding="utf-8")
    (repo / "hermes_cli").mkdir(parents=True)
    (repo / "hermes_cli" / "plugins.py").write_text("old\n", encoding="utf-8")
    # Build a minimal git history so git diff works.
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "hermes_cli" / "plugins.py").write_text(
        "VALID_HOOKS = {'before_persist_message'}\n"
        "invoke_hook('before_persist_message', agent=self, msg=msg)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    plan = build_plan(str(repo), "main", "feature")

    assert len(plan.files) == 1
    assert plan.files[0].risk_level == "HIGH"
    assert "hook:before_persist_message" in plan.files[0].risk_reasons
    assert any(r.relation_type == "hooks_into" and r.target_name == "before_persist_message" for r in plan.relations)
    assert any(r.relation_type == "breaks" for r in plan.relations)


def test_repo_root_from_finds_git_repo():
    assert Path(repo_root_from("/root/autolycus/repo/plugins/ontonet")).name == "repo"
