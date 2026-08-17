"""Aki adapter — the default, full-featured research harness.

This is the reference integration: the Aki harness (a personality layer + persistent
memory + a writable skills system + self-authored tools + an editable episode loop) plugged
into Proteus. It is the harness the paper's headline experiments run on.

Unlike the minimal harness, Aki is a real self-editing agent, so its episodes must run
under OS-level isolation (`proteus.sandbox.DockerSandbox`) — an authored tool executes as
arbitrary in-process code. The adapter therefore launches each episode as a container
command rather than in-process.

Status: this module defines the adapter surface and wiring. The concrete Aki package and
its containerized episode entrypoint live in the research repository; point
`AKI_HARNESS_SRC` at that checkout to activate. Without it, importing this adapter raises a
clear error telling you what to set — the minimal harness needs none of this and is the
zero-dependency path for trying Proteus.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from proteus.core.adapter import ActionEvent, EpisodeResult, EpisodeSpec, Surface
from proteus.core.disposition import Disposition


class AkiHarness:
    """`HarnessAdapter` for the Aki research harness (containerized)."""

    name = "aki"

    #: Surfaces Aki exposes. Names match the research measurement layer so results align.
    SURFACES = (
        Surface("memory", "memory", unit="file", write_tools=frozenset({"memory_write"})),
        Surface("skills", "skills", unit="directory", write_tools=frozenset({"skill_write"})),
        Surface("tools", "tools", unit="file", write_tools=frozenset({"tool_write"}),
                is_code=True),
        Surface("loop", "loop.py", unit="top_level_def", is_code=True, free_named=False),
    )

    def __init__(self, src: str | None = None) -> None:
        self.src = Path(src or os.environ.get("AKI_HARNESS_SRC", "")).expanduser()
        if not self.src or not self.src.exists():
            raise RuntimeError(
                "AkiHarness needs the Aki research checkout. Set AKI_HARNESS_SRC to it, or "
                "use the 'minimal' harness (no dependencies) to try Proteus."
            )

    def surfaces(self) -> Sequence[Surface]:
        return self.SURFACES

    def required_edit_tools(self) -> frozenset[str]:
        return frozenset({"memory_write", "skill_write", "file_write"})

    def seed(self, harness_root: Path) -> None:  # pragma: no cover - integration path
        raise NotImplementedError(
            "Aki seeding is provided by the research runner's init_run; wire it here when "
            "AKI_HARNESS_SRC is set. See docs/ADAPTERS.md."
        )

    def install_disposition(self, harness_root: Path, disposition: Disposition) -> None:  # pragma: no cover
        raise NotImplementedError

    def run_episode(self, spec: EpisodeSpec) -> EpisodeResult:  # pragma: no cover
        raise NotImplementedError

    def read_trace(self, root: Path, episode: int) -> Sequence[ActionEvent]:  # pragma: no cover
        raise NotImplementedError

    def disposition_fingerprint(self, harness_root: Path) -> str:  # pragma: no cover
        raise NotImplementedError
