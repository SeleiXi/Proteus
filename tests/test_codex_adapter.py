import json
import subprocess
from pathlib import Path

from proteus.adapters.codex import CodexHarness


def test_codex_json_trace_maps_tools_and_surfaces(tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    path = traces / "ep001-act.jsonl"
    rows = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "item.completed", "item": {
            "type": "command_execution", "command": "cargo check", "exit_code": 0}},
        {"type": "item.completed", "item": {
            "type": "file_change", "changes": [
                {"path": "/workspace/candidate/src/codex-rs/core/src/lib.rs",
                 "kind": "update"}]}},
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": "done"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    trace = CodexHarness(auth_home=tmp_path, sandbox=object()).read_trace(tmp_path, 1)
    assert [event.tool for event in trace] == ["bash", "apply_patch", None]
    assert trace[1].surface == "loop"
    assert trace[2].text == "done"


def test_codex_seed_declares_staged_candidate_paths():
    from proteus.adapters.codex import SEED_INSTRUCTIONS
    assert "/workspace/candidate/src/" in SEED_INSTRUCTIONS
    assert "next episode" in SEED_INSTRUCTIONS


def test_codex_source_extraction_uses_an_absolute_bind_mount(tmp_path, monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_run)
    CodexHarness(auth_home=tmp_path, sandbox=object())._extract_self_code(
        Path(tmp_path.name + "-relative")
    )
    mount = captured["args"][captured["args"].index("-v") + 1]
    assert mount == f"{tmp_path / (tmp_path.name + '-relative')}:/proteus-out"


def test_codex_missing_login_fails_without_launching(tmp_path):
    from proteus.core.adapter import EpisodeSpec
    adapter = CodexHarness(auth_home=tmp_path, sandbox=object())
    result = adapter.run_episode(EpisodeSpec(
        root=tmp_path, episode=1, model="", phase_prompts={}))
    assert not result.ok
    assert "not logged in" in result.error


def test_codex_budget_stop_is_a_cap_not_an_error(tmp_path, monkeypatch):
    from proteus.core.adapter import EpisodeSpec

    monkeypatch.chdir(tmp_path)
    run_root = tmp_path / "run"

    class Sandbox:
        calls = 0

        def run(self, root, args, env, timeout_s, mounts=(), stop_check=None):
            self.calls += 1
            assert Path(root).is_absolute()
            assert all(Path(item[0]).is_absolute() for item in mounts)
            records = next(item[0] for item in mounts if item[1] == "/records")
            trace_name = args[1].split("/")[-1]
            path = run_root / "traces" / trace_name
            assert str(path.parent) == records
            path.write_text(json.dumps({
                "type": "item.completed",
                "item": {"type": "command_execution", "command": "true"},
            }) + "\n", encoding="utf-8")
            fired = bool(stop_check and stop_check())
            return subprocess.CompletedProcess(args, 137 if fired else 0, "", "")

    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / "auth.json").write_text("{}", encoding="utf-8")
    (run_root / "harness").mkdir(parents=True)
    sandbox = Sandbox()
    adapter = CodexHarness(auth_home=auth, sandbox=sandbox)
    result = adapter.run_episode(EpisodeSpec(
        root=Path("run"), episode=1, model="", phase_prompts={}, max_turns=1))
    assert result.ok and result.turns == 1
    assert sandbox.calls == 1
