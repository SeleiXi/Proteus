import json
from pathlib import Path

from proteus.adapters.codex import CodexHarness


def test_surface_for_path():
    h = object.__new__(CodexHarness)
    assert h._surface_for_path('/workspace/candidate/AGENTS.md') == 'instructions'
    assert h._surface_for_path('/workspace/candidate/.agents/skills/foo/SKILL.md') == 'skills'
    assert h._surface_for_path('/workspace/candidate/src/codex-rs/core/src/lib.rs') == 'loop'
    assert h._surface_for_path('/workspace/task/foo.py') is None


def test_jsonl_trace_maps_codex_exec_events():
    h = object.__new__(CodexHarness)
    lines = [
        {"type": "item.completed", "item": {"id": "1", "type": "command_execution",
          "command": "cargo test", "aggregated_output": "ok", "exit_code": 0,
          "status": "completed"}},
        {"type": "item.completed", "item": {"id": "2", "type": "file_change",
          "changes": [{"path": "/workspace/candidate/src/codex-rs/core/src/lib.rs",
                       "kind": "update"}], "status": "completed"}},
        {"type": "item.completed", "item": {"id": "3", "type": "web_search",
          "query": "codex docs", "action": {}}},
        {"type": "item.completed", "item": {"id": "4", "type": "agent_message",
          "text": "done"}},
    ]
    trace = h._jsonl_trace("\n".join(json.dumps(x) for x in lines), "act")
    assert [e.tool for e in trace] == ["command", "file_change", "web_search", None]
    assert trace[1].surface == "loop"
    assert trace[-1].text == "done"


def test_read_trace_offsets_phases(tmp_path: Path):
    h = object.__new__(CodexHarness)
    sessions = tmp_path / '.codex-state' / 'sessions'
    sessions.mkdir(parents=True)
    (tmp_path / 'traces').mkdir()
    one = json.dumps({"type": "item.completed", "item": {
        "id": "1", "type": "command_execution", "command": "pwd",
        "aggregated_output": "", "exit_code": 0, "status": "completed"}})
    (sessions / 'ep001-observe.jsonl').write_text(one)
    (sessions / 'ep001-act.jsonl').write_text(one)
    (tmp_path / 'traces' / 'ep001.json').write_text(json.dumps({
        'observe': 'ep001-observe.jsonl', 'act': 'ep001-act.jsonl'}))
    trace = h.read_trace(tmp_path, 1)
    assert len(trace) == 2
    assert trace[0].turn == 1 and trace[1].turn == 2
    assert trace[0].phase == 'observe' and trace[1].phase == 'act'
