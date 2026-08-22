from pathlib import Path

from examples.dsh_audio_evolution import EPISTEMIC_CONDITION
from proteus.adapters import instructions
from proteus.bench.dsh_audio import EVALUATOR_SUFFICIENCY_PROTOCOL, capability_gates


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


def test_evaluator_sufficiency_protocol_leaves_judgment_to_the_harness(tmp_path):
    text = EVALUATOR_SUFFICIENCY_PROTOCOL
    normalized = " ".join(text.split())
    assert "may fully operationalize the stated goal" in normalized
    assert "Judge its sufficiency against the actual goal" in normalized
    assert "do not add them merely to satisfy this protocol" in normalized
    assert "If no external goal is supplied, do not assume one" in normalized
    assert "formulate or revise your own provisional goals" in normalized

    # The protocol is intentionally domain-neutral: none of the held-out audio findings
    # may leak into the subject's instructions.
    assert not any(term in text.lower() for term in (
        "audio", "flac", "transcriber", "wav", "zip", "duration",
    ))

    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Existing harness instructions\n", encoding="utf-8")
    assert EPISTEMIC_CONDITION.prompt_suffix == text
    instructions.install_block(agents, EPISTEMIC_CONDITION)
    installed = agents.read_text(encoding="utf-8")
    assert text.strip() in installed
    assert instructions.block_fingerprint(agents)
