from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from proteus.adapters.minimal import MinimalHarness, mock_policy
from proteus.core import NEUTRAL, GoalConfig, snapshot
from proteus.core.adapter import HarnessAdapter, Surface
from proteus.safety.model import (
    AuditAssessment,
    AuditContext,
    AuditMode,
    AuditObservation,
    AuditStatus,
    AuditTaxonomy,
    Exposure,
)
from proteus.safety.runner import run_audit
from proteus.sweep import SweepConfig, run_sweep


def _taxonomy() -> AuditTaxonomy:
    return AuditTaxonomy(
        target="trace",
        scope="local",
        initiating_source="framework",
        episode_phases=("reflect",),
        evolution_stages=("committed_state",),
        failure_mode="evidence_integrity",
        evidence_authority="independent_oracle",
        evidence_method=AuditMode.ARTIFACT.value,
    )


class PassingCase:
    case_id = "snapshot-state"
    taxonomy = _taxonomy()
    expected_behavior = ("the materialized snapshot is readable",)

    def evaluate(self, context: AuditContext) -> AuditAssessment:
        evidence = context.evidence_dir / "state.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps({"state": (context.snapshot_root / "STATE.md").read_text()}),
            encoding="utf-8",
        )
        return AuditAssessment(
            status=AuditStatus.PASS,
            exposure=Exposure.EXPOSED,
            observed_behavior=("the snapshot state was read",),
            evidence_refs=(evidence.relative_to(context.audit_root).as_posix(),),
            observation=AuditObservation(detected_or_contained=True),
        )


class ThrowingCase:
    case_id = "throwing-oracle"
    taxonomy = _taxonomy()
    expected_behavior = ("the oracle completes",)

    def evaluate(self, context: AuditContext) -> AuditAssessment:
        raise RuntimeError("oracle boom")


class EscapingCase(PassingCase):
    case_id = "../outside"


@dataclass
class FixtureSuite:
    selected_cases: tuple[object, ...] = (PassingCase(),)
    name: str = "fixture"
    version: str = "1"

    def cases(
        self, adapter: HarnessAdapter, surfaces: tuple[Surface, ...]
    ) -> tuple[object, ...]:
        return self.selected_cases


def _reflecting_policy(phase, prompt, episode, rng):
    if phase == "reflect":
        return [(None, None, "I believe this change is safe")]
    return mock_policy(phase, prompt, episode, rng)


def _completed_sweep(tmp_path: Path, *, episodes: int = 2, policy=mock_policy):
    root = tmp_path / "sweep"
    run_sweep(
        SweepConfig(
            name="fixture",
            adapter_factory=lambda: MinimalHarness(policy=policy),
            arms=(NEUTRAL,),
            seeds=1,
            goal=GoalConfig.no_goal(),
            root=root,
            model="mock",
            episodes=episodes,
        )
    )
    return root, MinimalHarness(policy=policy), FixtureSuite()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_audit_writes_only_sweep_sidecars(tmp_path: Path) -> None:
    sweep, adapter, suite = _completed_sweep(tmp_path)
    run_root = next((sweep / "runs").iterdir())
    before_state = (run_root / "harness" / "STATE.md").read_text()
    before_head = snapshot.head(run_root / "harness")
    before_eval = (run_root / "eval_history.json").read_text()
    before_seeds = (sweep / "seeds.jsonl").read_text()

    result = run_audit(sweep, adapter, suite, audit_id="fixture-v1")

    assert result.total_results == 2
    assert result.status_counts == {"pass": 2}
    assert (sweep / "audits/fixture-v1/results.jsonl").is_file()
    assert (sweep / "audits/index.json").is_file()
    assert (run_root / "harness" / "STATE.md").read_text() == before_state
    assert snapshot.head(run_root / "harness") == before_head
    assert (run_root / "eval_history.json").read_text() == before_eval
    assert (sweep / "seeds.jsonl").read_text() == before_seeds


def test_throwing_case_becomes_error_and_later_case_runs(tmp_path: Path) -> None:
    sweep, adapter, _ = _completed_sweep(tmp_path, episodes=1)
    suite = FixtureSuite(selected_cases=(ThrowingCase(), PassingCase()))

    result = run_audit(sweep, adapter, suite, audit_id="continues")

    rows = _read_jsonl(result.results_path)
    assert [row["status"] for row in rows] == ["error", "pass"]
    assert "oracle boom" in rows[0]["error"]


def test_existing_audit_id_is_never_overwritten(tmp_path: Path) -> None:
    sweep, adapter, suite = _completed_sweep(tmp_path, episodes=1)
    run_audit(sweep, adapter, suite, audit_id="same")
    original = (sweep / "audits/same/results.jsonl").read_text()

    with pytest.raises(FileExistsError, match="same"):
        run_audit(sweep, adapter, suite, audit_id="same")

    assert (sweep / "audits/same/results.jsonl").read_text() == original


def test_missing_episode_snapshot_is_invalid_not_pass(tmp_path: Path) -> None:
    sweep, adapter, suite = _completed_sweep(tmp_path, episodes=1)
    records_path = sweep / "seeds.jsonl"
    record = json.loads(records_path.read_text().splitlines()[0])
    record["episodes_complete"] = 2
    records_path.write_text(json.dumps(record) + "\n")

    result = run_audit(sweep, adapter, suite, audit_id="missing-snapshot")

    rows = _read_jsonl(result.results_path)
    assert rows[-1]["episode"] == 2
    assert rows[-1]["status"] == "invalid"
    assert result.status_counts == {"invalid": 1, "pass": 1}


def test_reflect_text_is_diagnostic_only(tmp_path: Path) -> None:
    sweep, adapter, suite = _completed_sweep(
        tmp_path, episodes=1, policy=_reflecting_policy
    )

    result = run_audit(sweep, adapter, suite, audit_id="self-claim")

    row = _read_jsonl(result.results_path)[0]
    assert row["self_assessments"] == ["I believe this change is safe"]
    assert row["status"] == "pass"


def test_missing_sweep_metadata_publishes_no_audit(tmp_path: Path) -> None:
    sweep = tmp_path / "missing"
    sweep.mkdir()

    with pytest.raises(FileNotFoundError):
        run_audit(sweep, MinimalHarness(), FixtureSuite(), audit_id="never-published")

    assert not (sweep / "audits").exists()


@pytest.mark.parametrize("audit_id", ["../escape", "has/slash", ""])
def test_invalid_explicit_audit_id_is_rejected(tmp_path: Path, audit_id: str) -> None:
    sweep, adapter, suite = _completed_sweep(tmp_path, episodes=1)
    if not audit_id:
        suite.name = "bad/name"

    with pytest.raises(ValueError, match="audit ID"):
        run_audit(sweep, adapter, suite, audit_id=audit_id)


def test_case_id_cannot_escape_audit_evidence_root(tmp_path: Path) -> None:
    sweep, adapter, _ = _completed_sweep(tmp_path, episodes=1)
    suite = FixtureSuite(selected_cases=(EscapingCase(),))

    with pytest.raises(ValueError, match="case ID"):
        run_audit(sweep, adapter, suite, audit_id="bad-case")

    assert not (sweep / "audits").exists()


def test_manifest_run_id_cannot_escape_sweep_root(tmp_path: Path) -> None:
    sweep, adapter, suite = _completed_sweep(tmp_path, episodes=1)
    manifest_path = sweep / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["runs"][0]["id"] = "../outside"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="run ID"):
        run_audit(sweep, adapter, suite, audit_id="bad-run")

    assert not (sweep / "audits").exists()
