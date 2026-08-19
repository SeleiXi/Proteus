import json
from pathlib import Path

import pytest

from proteus.adapters.minimal import MinimalHarness
from proteus.core import NEUTRAL, GoalConfig
from proteus.core.adapter import Surface
from proteus.safety import (
    AuditContext,
    AuditMode,
    AuditObservation,
    Exposure,
    SafetyEvidence,
    SafetyEvidenceAdapter,
    SafetyEvidenceProvider,
    SafetyEvidenceRequest,
)
from proteus.safety.evaluator import (
    SafetyMeasurementCase,
    SafetyMeasurementDefinition,
    SafetyMeasurementEvaluator,
)
from proteus.safety.model import AuditTaxonomy
from proteus.safety.runner import run_audit
from proteus.sweep import SweepConfig, run_sweep


class FixtureProvider:
    name = "fixture"

    def collect(self, request, context):
        raise AssertionError("collection is outside this contract test")


class FixtureAdapter:
    def safety_evidence_provider(self) -> SafetyEvidenceProvider:
        return FixtureProvider()


class LiteralProvider:
    name = "literal"

    def __init__(self, evidence: SafetyEvidence) -> None:
        self.evidence = evidence

    def collect(self, request, context) -> SafetyEvidence:
        return self.evidence


class ProviderAdapter:
    def __init__(self, provider: SafetyEvidenceProvider) -> None:
        self.provider = provider

    def safety_evidence_provider(self) -> SafetyEvidenceProvider:
        return self.provider


class MutatingReplayProvider:
    name = "mutating-replay"

    def collect(self, request, context) -> SafetyEvidence:
        (context.snapshot_root / "provider-owned.txt").write_text("changed")
        return SafetyEvidence(
            mode=AuditMode.CONTAINED_REPLAY,
            evaluable=True,
            evidence_refs=("evidence/replay.json",),
            observation=AuditObservation(safety_invariant_violated=False),
        )


class RecordingReplayProvider:
    name = "recording-replay"

    def __init__(self, *, raises_for: frozenset[str] = frozenset()) -> None:
        self.raises_for = raises_for
        self.received_states: list[str] = []

    def collect(self, request, context) -> SafetyEvidence:
        received_state = (context.snapshot_root / "STATE.md").read_text(encoding="utf-8")
        self.received_states.append(received_state)
        evidence = context.evidence_dir / "provider-evidence.json"
        evidence.write_text(
            json.dumps({"scenario": request.scenario, "state": received_state}) + "\n",
            encoding="utf-8",
        )
        (context.snapshot_root / "STATE.md").write_text("provider mutation\n", encoding="utf-8")
        if request.scenario in self.raises_for:
            raise RuntimeError(f"provider boom: {request.scenario}")
        return SafetyEvidence(
            mode=AuditMode.CONTAINED_REPLAY,
            evaluable=True,
            evidence_refs=(evidence.relative_to(context.audit_root).as_posix(),),
            observation=AuditObservation(
                safety_invariant_violated=request.scenario == "violated"
            ),
        )


def _taxonomy() -> AuditTaxonomy:
    return AuditTaxonomy(
        target="generic-agent",
        scope="local",
        initiating_source="external-instrument",
        episode_phases=("act",),
        evolution_stages=("committed_state",),
        failure_mode="invariant_violation",
        evidence_authority="independent_oracle",
        evidence_method=AuditMode.ARTIFACT.value,
    )


def _definition(mode: AuditMode = AuditMode.ARTIFACT) -> SafetyMeasurementDefinition:
    return SafetyMeasurementDefinition(
        case_id="controlled-invariant",
        taxonomy=_taxonomy(),
        expected_behavior=("the controlled invariant holds",),
        failure="the controlled invariant was violated",
        request=SafetyEvidenceRequest(mode=mode, scenario="literal-scenario"),
    )


def _replay_definition(case_id: str, scenario: str) -> SafetyMeasurementDefinition:
    return SafetyMeasurementDefinition(
        case_id=case_id,
        taxonomy=AuditTaxonomy(
            target="generic-agent",
            scope="local",
            initiating_source="external-instrument",
            episode_phases=("act",),
            evolution_stages=("committed_state",),
            failure_mode="invariant_violation",
            evidence_authority="independent_oracle",
            evidence_method=AuditMode.CONTAINED_REPLAY.value,
        ),
        expected_behavior=("the replayed safety invariant holds",),
        failure="the replayed safety invariant was violated",
        request=SafetyEvidenceRequest(mode=AuditMode.CONTAINED_REPLAY, scenario=scenario),
    )


def _completed_sweep(tmp_path: Path) -> tuple[Path, MinimalHarness, str]:
    sweep_root = tmp_path / "sweep"
    run_sweep(
        SweepConfig(
            name="safety-evaluator-integration",
            adapter_factory=MinimalHarness,
            arms=(NEUTRAL,),
            seeds=1,
            goal=GoalConfig.no_goal(),
            root=sweep_root,
            model="mock",
            episodes=1,
        )
    )
    run_root = next((sweep_root / "runs").iterdir())
    source_state = (run_root / "harness" / "STATE.md").read_text(encoding="utf-8")
    return sweep_root, MinimalHarness(), source_state


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _context(tmp_path: Path) -> AuditContext:
    snapshot_root = tmp_path / "snapshot"
    snapshot_root.mkdir()
    return AuditContext(
        audit_root=tmp_path / "audit",
        evidence_dir=tmp_path / "audit/evidence/run-1/case",
        run_id="run-1",
        adapter_name="fixture",
        arm="neutral",
        seed=0,
        episode=1,
        snapshot_root=snapshot_root,
        surfaces=(Surface("notes", "notes"),),
        events=(),
        self_assessments=(),
    )


def test_evidence_request_preserves_provider_owned_parameters() -> None:
    parameters = {"native_case": "composition/x1", "controlled_input": {"arm": "probe"}}

    request = SafetyEvidenceRequest(
        mode=AuditMode.CONTAINED_REPLAY,
        scenario="provider-owned-scenario",
        parameters=parameters,
    )

    assert request.mode is AuditMode.CONTAINED_REPLAY
    assert request.scenario == "provider-owned-scenario"
    assert request.parameters == parameters


def test_evidence_keeps_exposure_independent_from_evaluability() -> None:
    evidence = SafetyEvidence(
        mode=AuditMode.CONTAINED_REPLAY,
        evaluable=True,
        exposure=Exposure.NOT_EXPOSED,
        observed_behavior=("controlled operation was unavailable",),
        observation=AuditObservation(safety_invariant_violated=False),
    )

    assert evidence.evaluable is True
    assert evidence.exposure is Exposure.NOT_EXPOSED
    assert evidence.observation.safety_invariant_violated is False


def test_evidence_provider_adapter_is_discovered_structurally() -> None:
    adapter = FixtureAdapter()

    assert isinstance(adapter, SafetyEvidenceAdapter)
    assert adapter.safety_evidence_provider().name == "fixture"


def test_safe_determinate_evidence_passes(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=True,
                evidence_refs=("evidence/observation.json",),
                observation=AuditObservation(safety_invariant_violated=False),
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "pass"


def test_violated_invariant_fails_with_definition_failure(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=True,
                evidence_refs=("evidence/observation.json",),
                observation=AuditObservation(safety_invariant_violated=True),
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "fail"
    assert assessment.failures == ("the controlled invariant was violated",)


def test_missing_provider_is_not_evaluated(tmp_path: Path) -> None:
    assessment = SafetyMeasurementCase(_definition(), None).evaluate(_context(tmp_path))

    assert assessment.status.value == "not_evaluated"


def test_unevaluable_evidence_with_reason_is_not_evaluated(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=False,
                reason="the required native event was unavailable",
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "not_evaluated"
    assert assessment.observed_behavior == ("the required native event was unavailable",)


def test_not_exposed_is_not_evaluated_not_pass(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=False,
                exposure=Exposure.NOT_EXPOSED,
                reason="the controlled operation was unavailable",
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "not_evaluated"
    assert assessment.exposure.value == "not_exposed"


def test_evaluable_not_exposed_evidence_is_not_a_pass(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=True,
                exposure=Exposure.NOT_EXPOSED,
                evidence_refs=("evidence/observation.json",),
                observation=AuditObservation(safety_invariant_violated=False),
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "not_evaluated"
    assert assessment.exposure.value == "not_exposed"


def test_not_exposed_violated_invariant_is_invalid(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=True,
                exposure=Exposure.NOT_EXPOSED,
                evidence_refs=("evidence/observation.json",),
                observation=AuditObservation(safety_invariant_violated=True),
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "invalid"
    assert assessment.exposure.value == "not_exposed"


@pytest.mark.parametrize(
    "ref",
    ["", "   ", ".", "/tmp/evidence.json", "../evidence.json", "a/../../b"],
)
def test_malformed_provider_evidence_ref_is_invalid_without_the_ref(
    tmp_path: Path, ref: str
) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=True,
                evidence_refs=(ref,),
                observation=AuditObservation(safety_invariant_violated=False),
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "invalid"
    assert assessment.evidence_refs == ()


@pytest.mark.parametrize("failure", ["", "   "])
def test_measurement_definition_rejects_blank_failure(failure: str) -> None:
    with pytest.raises(ValueError, match="failure"):
        SafetyMeasurementDefinition(
            case_id="controlled-invariant",
            taxonomy=_taxonomy(),
            expected_behavior=("the controlled invariant holds",),
            failure=failure,
            request=SafetyEvidenceRequest(
                mode=AuditMode.ARTIFACT,
                scenario="literal-scenario",
            ),
        )


def test_mode_mismatch_is_invalid(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.CONTAINED_REPLAY,
                evaluable=True,
                evidence_refs=("evidence/observation.json",),
                observation=AuditObservation(safety_invariant_violated=False),
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "invalid"


def test_unevaluable_evidence_without_reason_is_invalid(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(SafetyEvidence(mode=AuditMode.ARTIFACT, evaluable=False)),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "invalid"


def test_unevaluable_evidence_with_determinate_invariant_is_invalid(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=False,
                reason="the required native event was unavailable",
                observation=AuditObservation(safety_invariant_violated=True),
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "invalid"


def test_evaluable_evidence_without_invariant_is_invalid(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=True,
                evidence_refs=("evidence/observation.json",),
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "invalid"


def test_evaluable_evidence_without_references_is_invalid(tmp_path: Path) -> None:
    case = SafetyMeasurementCase(
        _definition(),
        LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=True,
                observation=AuditObservation(safety_invariant_violated=False),
            )
        ),
    )

    assessment = case.evaluate(_context(tmp_path))

    assert assessment.status.value == "invalid"


def test_explicit_provider_takes_precedence_over_adapter_provider(tmp_path: Path) -> None:
    explicit_provider = LiteralProvider(
        SafetyEvidence(
            mode=AuditMode.ARTIFACT,
            evaluable=True,
            evidence_refs=("evidence/explicit.json",),
            observation=AuditObservation(safety_invariant_violated=False),
        )
    )
    adapter_provider = LiteralProvider(
        SafetyEvidence(
            mode=AuditMode.ARTIFACT,
            evaluable=True,
            evidence_refs=("evidence/adapter.json",),
            observation=AuditObservation(safety_invariant_violated=True),
        )
    )
    suite = SafetyMeasurementEvaluator(
        name="fixture", version="1", definitions=(_definition(),), provider=explicit_provider
    )

    assessment = suite.cases(ProviderAdapter(adapter_provider), ())[0].evaluate(
        _context(tmp_path)
    )

    assert assessment.status.value == "pass"


def test_adapter_provider_is_used_when_no_explicit_provider_is_supplied(
    tmp_path: Path,
) -> None:
    adapter_provider = LiteralProvider(
        SafetyEvidence(
            mode=AuditMode.ARTIFACT,
            evaluable=True,
            evidence_refs=("evidence/adapter.json",),
            observation=AuditObservation(safety_invariant_violated=False),
        )
    )
    suite = SafetyMeasurementEvaluator(
        name="fixture", version="1", definitions=(_definition(),)
    )

    assessment = suite.cases(ProviderAdapter(adapter_provider), ())[0].evaluate(
        _context(tmp_path)
    )

    assert assessment.status.value == "pass"


def test_replay_provider_receives_a_disposable_snapshot_copy(tmp_path: Path) -> None:
    context = _context(tmp_path)
    case = SafetyMeasurementCase(_definition(AuditMode.CONTAINED_REPLAY), MutatingReplayProvider())

    assessment = case.evaluate(context)

    assert assessment.status.value == "pass"
    assert not (context.snapshot_root / "provider-owned.txt").exists()


def test_evaluator_replay_publishes_sidecars_and_isolates_cases_and_source(
    tmp_path: Path,
) -> None:
    sweep_root, adapter, source_state = _completed_sweep(tmp_path)
    provider = RecordingReplayProvider()
    evaluator = SafetyMeasurementEvaluator(
        name="generic-replay",
        version="1",
        definitions=(
            _replay_definition("safe-replay", "safe"),
            _replay_definition("violated-replay", "violated"),
        ),
        provider=provider,
    )

    summary = run_audit(sweep_root, adapter, evaluator, audit_id="generic-replay-v1")

    assert summary.status_counts == {"fail": 1, "pass": 1}
    assert summary.audit_root == sweep_root / "audits/generic-replay-v1"
    assert (summary.audit_root / "manifest.json").is_file()
    assert summary.results_path.is_file()
    assert summary.summary_path.is_file()
    manifest = json.loads((summary.audit_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["audit_id"] == "generic-replay-v1"
    assert manifest["suite"] == "generic-replay"
    assert manifest["source_sweep"] == str(sweep_root.resolve())
    rows = _read_jsonl(summary.results_path)
    assert [row["status"] for row in rows] == ["pass", "fail"]
    assert json.loads(summary.summary_path.read_text(encoding="utf-8"))["status_counts"] == {
        "fail": 1,
        "pass": 1,
    }
    evidence_paths = [
        summary.audit_root / row["evidence_refs"][0]
        for row in rows
    ]
    assert [json.loads(path.read_text(encoding="utf-8"))["scenario"] for path in evidence_paths] == [
        "safe",
        "violated",
    ]
    assert provider.received_states == [source_state, source_state]
    run_root = next((sweep_root / "runs").iterdir())
    assert (run_root / "harness" / "STATE.md").read_text(encoding="utf-8") == source_state
    index = json.loads((sweep_root / "audits/index.json").read_text(encoding="utf-8"))
    assert index["audits"][0]["id"] == "generic-replay-v1"
    assert index["audits"][0]["results"] == "generic-replay-v1/results.jsonl"


def test_malformed_provider_ref_publishes_invalid_sidecar_not_error(tmp_path: Path) -> None:
    sweep_root, adapter, _ = _completed_sweep(tmp_path)
    evaluator = SafetyMeasurementEvaluator(
        name="malformed-provider-ref",
        version="1",
        definitions=(_definition(),),
        provider=LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=True,
                evidence_refs=("../outside-audit.json",),
                observation=AuditObservation(safety_invariant_violated=False),
            )
        ),
    )

    summary = run_audit(sweep_root, adapter, evaluator, audit_id="malformed-provider-ref-v1")

    row = _read_jsonl(summary.results_path)[0]
    assert row["status"] == "invalid"
    assert row["evidence_refs"] == []
    assert row["error"] == ""


def test_unevaluable_reason_is_published_in_sidecar_observed_behavior(
    tmp_path: Path,
) -> None:
    sweep_root, adapter, _ = _completed_sweep(tmp_path)
    evaluator = SafetyMeasurementEvaluator(
        name="unevaluable-reason",
        version="1",
        definitions=(_definition(),),
        provider=LiteralProvider(
            SafetyEvidence(
                mode=AuditMode.ARTIFACT,
                evaluable=False,
                reason="  required observation unavailable  ",
            )
        ),
    )

    summary = run_audit(sweep_root, adapter, evaluator, audit_id="unevaluable-reason-v1")

    row = _read_jsonl(summary.results_path)[0]
    assert row["status"] == "not_evaluated"
    assert row["observed_behavior"] == ["required observation unavailable"]


def test_throwing_replay_provider_becomes_error_and_later_definition_runs(
    tmp_path: Path,
) -> None:
    sweep_root, adapter, _ = _completed_sweep(tmp_path)
    provider = RecordingReplayProvider(raises_for=frozenset({"throws"}))
    evaluator = SafetyMeasurementEvaluator(
        name="provider-errors",
        version="1",
        definitions=(
            _replay_definition("throws", "throws"),
            _replay_definition("safe-after-error", "safe"),
        ),
        provider=provider,
    )

    summary = run_audit(sweep_root, adapter, evaluator, audit_id="provider-errors-v1")

    rows = _read_jsonl(summary.results_path)
    assert [row["status"] for row in rows] == ["error", "pass"]
    assert "provider boom: throws" in rows[0]["error"]
    assert len(provider.received_states) == 2
    assert provider.received_states[1] == provider.received_states[0]
