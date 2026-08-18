from __future__ import annotations

import json
from pathlib import Path

import pytest

from proteus.adapters.minimal import MinimalHarness
from proteus.cli import main
from proteus.core import NEUTRAL, GoalConfig
from proteus.sweep import SweepConfig, run_sweep


def _make_sweep(tmp_path: Path) -> Path:
    root = tmp_path / "sweep"
    run_sweep(
        SweepConfig(
            name="fixture",
            adapter_factory=MinimalHarness,
            arms=(NEUTRAL,),
            seeds=1,
            goal=GoalConfig.no_goal(),
            root=root,
            model="mock",
            episodes=1,
        )
    )
    return root


def test_audit_command_writes_completed_index(tmp_path: Path, capfd) -> None:
    sweep = _make_sweep(tmp_path)

    code = main(
        [
            "audit",
            "--harness",
            "minimal",
            "--out",
            str(sweep),
            "--audit-id",
            "cli-integrity",
        ]
    )

    assert code == 0
    index = json.loads((sweep / "audits/index.json").read_text())
    assert index["audits"][0]["id"] == "cli-integrity"
    captured = capfd.readouterr()
    assert "audit results" in captured.out
    assert captured.err == ""


def test_audit_command_returns_two_instead_of_overwriting(
    tmp_path: Path, capsys
) -> None:
    sweep = _make_sweep(tmp_path)
    args = [
        "audit",
        "--harness",
        "minimal",
        "--out",
        str(sweep),
        "--audit-id",
        "same",
    ]
    assert main(args) == 0
    original = (sweep / "audits/same/results.jsonl").read_text()

    assert main(args) == 2

    assert "already exists" in capsys.readouterr().err
    assert (sweep / "audits/same/results.jsonl").read_text() == original


def test_audit_command_reports_bad_suite_spec(tmp_path: Path, capsys) -> None:
    sweep = _make_sweep(tmp_path)

    code = main(
        [
            "audit",
            "--harness",
            "minimal",
            "--out",
            str(sweep),
            "--suite",
            "bad-spec",
        ]
    )

    assert code == 2
    assert "<module>:<object>" in capsys.readouterr().err
    assert not (sweep / "audits").exists()


def test_audit_help_states_post_run_boundary(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["audit", "--help"])

    assert caught.value.code == 0
    assert "completed evolution sweep without changing it" in capsys.readouterr().out


def test_audit_command_rejects_incomplete_sweep(tmp_path: Path, capsys) -> None:
    sweep = _make_sweep(tmp_path)
    manifest_path = sweep / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["episodes"] = 2
    manifest_path.write_text(json.dumps(manifest))

    code = main(["audit", "--harness", "minimal", "--out", str(sweep)])

    assert code == 2
    assert "incomplete" in capsys.readouterr().err
    assert not (sweep / "audits").exists()


def test_audit_command_normalizes_unknown_harness(tmp_path: Path, capsys) -> None:
    sweep = _make_sweep(tmp_path)

    code = main(["audit", "--harness", "unknown", "--out", str(sweep)])

    assert code == 2
    error = capsys.readouterr().err
    assert "audit failed" in error
    assert "unknown harness" in error
    assert not (sweep / "audits").exists()
