from pathlib import Path

import pytest

from examples.dsh_audio_evolution import SnapshotSeededDshHarness
from proteus.bench.dsh_audio import capability_gates


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_empty_tree_scores_no_audio_gates(tmp_path):
    assert not any(capability_gates(tmp_path).values())


def test_cross_layer_audio_shape_scores_every_gate(tmp_path):
    _write(tmp_path, "packages/llm/llm/src/types.ts", """
        interface AudioBlock { type: 'audio' }
        interface ContentBlockMap { 'audio': AudioBlock }
    """)
    _write(tmp_path, "packages/attachment/audio.ts", """
        type AudioMediaType = 'audio/wav'; interface AudioAttachmentRef {}
        function saveAudio() {} function readAudio() {}
    """)
    _write(tmp_path, "packages/acp/acp/src/content.ts", """
        switch (block.type) { case 'audio': return persistAudio(block) }
    """)
    _write(tmp_path, "packages/client/ui-attachment/audio.tsx", """
        // composer draft audio attachment drop target: audio/wav
        export const AudioPlayer = () => <audio controls />
    """)
    _write(tmp_path, "packages/transcription/service.ts", """
        interface TranscriptionProvider { transcribeAudio(): Promise<string> }
    """)
    for area in ("attachment", "acp", "client", "llm"):
        _write(tmp_path, f"packages/{area}/tests/audio.spec.ts",
               "test('AudioAttachment transcription', () => {})")
    assert all(capability_gates(tmp_path).values())


def test_acp_rejection_does_not_count_as_admission(tmp_path):
    _write(tmp_path, "packages/acp/acp/src/content.ts", """
        case 'audio': throw new Error('audio prompt content is not supported')
        function persistAudio() {}
    """)
    assert not capability_gates(tmp_path)["ACP admission"]


def test_continuation_seed_copies_a_valid_harness_snapshot(tmp_path):
    source = tmp_path / "parent" / "harness"
    _write(source, "AGENTS.md", "instructions\n")
    _write(source, "src/package.json", '{"name":"continued"}\n')
    _write(source, "notes/learned.md", "persistent state\n")
    handoff = tmp_path / "parent" / "latest.md"
    handoff.write_text("# Prior handoff\n\nContinue the validation.\n")

    adapter = SnapshotSeededDshHarness(source, handoff)
    destination = tmp_path / "child" / "harness"
    adapter.seed(destination)

    assert (destination / "src/package.json").read_text() == '{"name":"continued"}\n'
    assert (destination / "notes/learned.md").read_text() == "persistent state\n"
    assert (destination.parent / ".proteus-state/latest.md").read_text() == \
        "# Prior handoff\n\nContinue the validation.\n"
    with pytest.raises(FileExistsError, match="destination already exists"):
        adapter.seed(destination)


def test_continuation_seed_rejects_a_non_harness_directory(tmp_path):
    source = tmp_path / "not-a-harness"
    source.mkdir()
    adapter = SnapshotSeededDshHarness(source)

    with pytest.raises(ValueError, match="not a DSH harness snapshot"):
        adapter.seed(tmp_path / "child" / "harness")
