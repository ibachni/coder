"""Tests for build-command detection (src/bootstrap.py) and the repo_bootstrap_check node."""

from pathlib import Path

import pytest

from bootstrap import BootstrapConfig, BootstrapError, detect_commands
from classes import AgentState, Status
from ledger import read_json
from nodes.general.nodes import repo_bootstrap_check


class TestDetectCommands:
    def test_uv_project_uses_uv_run_prefix(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / "src").mkdir()
        cfg = detect_commands(tmp_path)
        assert cfg.test_cmd == ["uv", "run", "pytest"]
        assert cfg.lint_cmd == ["uv", "run", "ruff", "check", "src"]
        assert cfg.typecheck_cmd == ["uv", "run", "pyright", "src"]

    def test_plain_python_project_has_no_prefix(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "src").mkdir()
        cfg = detect_commands(tmp_path)
        assert cfg.test_cmd == ["pytest"]
        assert cfg.lint_cmd == ["ruff", "check", "src"]

    def test_targets_src_when_present(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "src").mkdir()
        cfg = detect_commands(tmp_path)
        assert cfg.lint_cmd[-1] == "src"
        assert cfg.typecheck_cmd[-1] == "src"

    def test_falls_back_to_repo_root_without_src(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")  # no src/ dir
        cfg = detect_commands(tmp_path)
        assert cfg.lint_cmd[-1] == "."
        assert cfg.typecheck_cmd[-1] == "."

    def test_unrecognized_layout_raises(self, tmp_path: Path) -> None:
        with pytest.raises(BootstrapError):
            detect_commands(tmp_path)

    def test_install_ok_false_for_uv_without_venv(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "uv.lock").write_text("")
        assert detect_commands(tmp_path).install_ok is False

    def test_install_ok_true_for_uv_with_venv(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / ".venv").mkdir()
        assert detect_commands(tmp_path).install_ok is True

    def test_returns_bootstrap_config(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        assert isinstance(detect_commands(tmp_path), BootstrapConfig)


class TestRepoBootstrapCheckNode:
    def _state(self, repo: Path) -> AgentState:
        return AgentState(status=Status.CONT, step=0, artifact={}, ticket_id="42", repo_path=repo)

    def test_writes_bootstrap_json_and_advances(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        out = repo_bootstrap_check(self._state(tmp_path))

        assert out.status is Status.CONT
        assert out.step == 1
        cfg = read_json(tmp_path / ".coder" / "runs" / "42" / "bootstrap.json")
        assert cfg["test_cmd"] == ["pytest"]
        assert cfg["install_ok"] is True

    def test_fails_when_commands_undetectable(self, tmp_path: Path) -> None:
        out = repo_bootstrap_check(self._state(tmp_path))
        assert out.status is Status.FAILURE
        assert out.step == 0  # did not advance
        assert not (tmp_path / ".coder").exists()
