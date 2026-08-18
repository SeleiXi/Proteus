from __future__ import annotations

import json
from pathlib import Path

from proteus.adapters.minimal import MinimalHarness
from proteus.core import NEUTRAL, GoalConfig
from proteus.core.adapter import ActionEvent
from proteus.safety.integrity import SUITE
from proteus.safety.model import AuditContext, AuditStatus, Exposure
from proteus.safety.runner import run_audit
from proteus.sweep import SweepConfig, run_sweep


def _completed_sweep(tmp_path: Path) -> tuple[Path, MinimalHarness]:
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
    return root, MinimalHarness()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_integrity_suite_reports_substrate_without_general_safety_score(
    tmp_path: Path,
) -> None:
    sweep, adapter = _completed_sweep(tmp_path)

    result = run_audit(sweep, adapter, SUITE, audit_id="integrity")

    rows = _read_jsonl(result.results_path)
    assert {row["case_id"] for row in rows} == {
        "snapshot-readable",
        "trace-readable",
        "trace-canonical-phases",
        "self-assessment-signal",
    }
    by_case = {row["case_id"]: row for row in rows}
    for case_id in (
        "snapshot-readable",
        "trace-readable",
        "trace-canonical-phases",
    ):
        assert by_case[case_id]["status"] == "pass"
    assert by_case["self-assessment-signal"]["status"] == "not_evaluated"
    assert by_case["self-assessment-signal"]["exposure"] == "not_exposed"
    for row in rows:
        if row["status"] in {"pass", "fail"}:
            assert row["evidence_refs"]
            assert all(not Path(ref).is_absolute() for ref in row["evidence_refs"])
    summary = json.loads(result.summary_path.read_text())
    assert "score" not in summary


def test_canonical_phase_case_marks_malformed_trace_invalid(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    evidence_dir = audit_root / "evidence"
    evidence_dir.mkdir(parents=True)
    adapter = MinimalHarness()
    case = next(case for case in SUITE.cases(adapter, adapter.surfaces())
                if case.case_id == "trace-canonical-phases")
    context = AuditContext(
        audit_root=audit_root,
        evidence_dir=evidence_dir,
        run_id="run-1",
        adapter_name="minimal",
        arm="neutral",
        seed=0,
        episode=1,
        snapshot_root=tmp_path / "snapshot",
        surfaces=tuple(adapter.surfaces()),
        events=(ActionEvent(turn=1, phase="unexpected"),),
        self_assessments=(),
    )

    assessment = case.evaluate(context)

    assert assessment.status is AuditStatus.INVALID
    assert assessment.exposure is Exposure.EXPOSED
    assert "unexpected" in assessment.failures[0]
    assert assessment.evidence_refs == ("evidence/phases.json",)
