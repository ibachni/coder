"""Tests for the shared node helpers in src/nodes/helpers.py.

`run_agent` is tested with a monkeypatched `subprocess.run` (no real `claude`
spawn); `parse_json_block` is exercised against the messy shapes agents actually
emit (fences, surrounding prose, objects vs arrays).
"""

import json
import subprocess
from pathlib import Path

import pytest

import nodes.helpers as helpers
from nodes.helpers import parse_json_block, run_agent, slugify


class TestSlugify:
    def test_lowercases_and_dashes(self) -> None:
        assert slugify("Add a Field!") == "add-a-field"

    def test_empty_falls_back(self) -> None:
        assert slugify("!!!") == "untitled"


class TestRunAgent:
    def test_wires_claude_invocation(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-stripped")
        captured: dict = {}

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        monkeypatch.setattr(helpers.subprocess, "run", fake_run)

        result = run_agent("do the thing", tmp_path, timeout=5)

        assert result.stdout == "ok"
        assert captured["cmd"] == ["claude", "-p", "do the thing"]
        assert captured["kwargs"]["cwd"] == tmp_path
        assert captured["kwargs"]["timeout"] == 5
        assert captured["kwargs"]["text"] is True
        assert captured["kwargs"]["capture_output"] is True
        env = captured["kwargs"]["env"]
        assert "ANTHROPIC_API_KEY" not in env  # subscription env strips it
        assert "CLAUDE_CODE_OAUTH_TOKEN" in env

    def test_does_not_raise_on_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        monkeypatch.setattr(helpers.subprocess, "run", fake_run)
        # Callers inspect returncode themselves; the helper must not raise.
        assert run_agent("p", tmp_path).returncode == 1


class TestParseJsonBlock:
    def test_bare_object(self) -> None:
        assert parse_json_block('{"a": 1}') == {"a": 1}

    def test_bare_array(self) -> None:
        assert parse_json_block('[{"x": 2}]') == [{"x": 2}]

    def test_strips_json_fence(self) -> None:
        assert parse_json_block('```json\n{"a": 1}\n```') == {"a": 1}

    def test_strips_bare_fence(self) -> None:
        assert parse_json_block("```\n[]\n```") == []

    def test_locates_object_amid_prose(self) -> None:
        assert parse_json_block('Here you go:\n{"a": 1}\nDone.') == {"a": 1}

    def test_locates_array_amid_prose(self) -> None:
        assert parse_json_block("Sure: [1, 2] cheers") == [1, 2]

    def test_object_with_inner_array_returns_whole_object(self) -> None:
        # The leading bracket is `{`, so the whole object (not the inner array) is decoded.
        assert parse_json_block('{"changes": [1, 2], "version": 3}') == {
            "changes": [1, 2],
            "version": 3,
        }

    def test_ignores_trailing_prose_containing_a_brace(self) -> None:
        # `raw_decode` stops at the object's real end; the stray `{x}` after it is ignored.
        assert parse_json_block('{"a": 1}. Note: see {x} later.') == {"a": 1}

    def test_fence_after_leading_prose(self) -> None:
        # Fence isn't at the very start, so the strip is skipped; bracket-locate still wins.
        assert parse_json_block('Here you go:\n```json\n{"a": 1}\n```') == {"a": 1}

    def test_invalid_raises_json_decode_error(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            parse_json_block("not json at all")

    def test_truncated_object_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            parse_json_block('{"a": 1')
