"""Tests for auto_skill.py CLI script."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _run_script(*args: str) -> subprocess.CompletedProcess:
    """Helper: run auto_skill.py from the scripts directory."""
    scripts_dir = os.path.join(os.path.dirname(__file__), "..")
    script_path = os.path.join(scripts_dir, "auto_skill.py")
    cmd = [sys.executable, script_path, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=os.path.join(scripts_dir, ".."),  # repo root
    )


class TestAutoSkill:
    """Tests for the auto_skill.py CLI entry point."""

    def test_auto_skill_ford(self):
        """'форд эксплорер проблема' → JSON with ford_diagnostics, confidence > 0.5."""
        result = _run_script("форд эксплорер проблема")
        assert result.returncode == 0, f"stderr: {result.stderr}"
        output = json.loads(result.stdout.strip())
        assert output["name"] == "ford_diagnostics"
        assert output["confidence"] > 0.5

    def test_auto_skill_unknown(self):
        """'как дела?' → {} (confidence < 0.5, empty JSON)."""
        result = _run_script("как дела?")
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert result.stdout.strip() == "{}"

    def test_auto_skill_no_args(self):
        """No arguments → exit code 1."""
        result = _run_script()
        assert result.returncode == 1