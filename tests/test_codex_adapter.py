import json

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


def test_codex_missing_login_fails_without_launching(tmp_path):
    from proteus.core.adapter import EpisodeSpec
    adapter = CodexHarness(auth_home=tmp_path, sandbox=object())
    result = adapter.run_episode(EpisodeSpec(
        root=tmp_path, episode=1, model="", phase_prompts={}))
    assert not result.ok
    assert "not logged in" in result.error
