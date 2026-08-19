from pathlib import Path

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
