from proteus.safety import (
    AuditMode,
    Exposure,
    SafetyEvidence,
    SafetyEvidenceAdapter,
    SafetyEvidenceProvider,
    SafetyEvidenceRequest,
)


class FixtureProvider:
    name = "fixture"

    def collect(self, request, context):
        raise AssertionError("collection is outside this contract test")


class FixtureAdapter:
    def safety_evidence_provider(self) -> SafetyEvidenceProvider:
        return FixtureProvider()


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
    )

    assert evidence.evaluable is True
    assert evidence.exposure is Exposure.NOT_EXPOSED


def test_evidence_provider_adapter_is_discovered_structurally() -> None:
    adapter = FixtureAdapter()

    assert isinstance(adapter, SafetyEvidenceAdapter)
    assert adapter.safety_evidence_provider().name == "fixture"
