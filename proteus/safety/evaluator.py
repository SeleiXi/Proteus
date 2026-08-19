"""Generic mapping from provider evidence to independent safety verdicts."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from proteus.core.adapter import HarnessAdapter, Surface
from proteus.safety.model import (
    AuditAssessment,
    AuditCase,
    AuditContext,
    AuditMode,
    AuditStatus,
    AuditTaxonomy,
    Exposure,
    SafetyEvidence,
    SafetyEvidenceAdapter,
    SafetyEvidenceProvider,
    SafetyEvidenceRequest,
    validate_evidence_refs,
)


@dataclass(frozen=True)
class SafetyMeasurementDefinition:
    """One provider-owned scenario and the invariant it measures."""

    case_id: str
    taxonomy: AuditTaxonomy
    expected_behavior: tuple[str, ...]
    failure: str
    request: SafetyEvidenceRequest

    def __post_init__(self) -> None:
        if not self.failure.strip():
            raise ValueError("safety measurement failure must be non-empty")


@dataclass(frozen=True)
class SafetyMeasurementCase:
    """Evaluate one definition with an optional evidence provider."""

    definition: SafetyMeasurementDefinition
    provider: SafetyEvidenceProvider | None

    @property
    def case_id(self) -> str:
        return self.definition.case_id

    @property
    def taxonomy(self) -> AuditTaxonomy:
        return self.definition.taxonomy

    @property
    def expected_behavior(self) -> tuple[str, ...]:
        return self.definition.expected_behavior

    def evaluate(self, context: AuditContext) -> AuditAssessment:
        if self.provider is None:
            return AuditAssessment(
                status=AuditStatus.NOT_EVALUATED,
                observed_behavior=("no safety evidence provider is available",),
            )

        evidence = self._collect_evidence(context)
        return self._assessment(evidence)

    def _collect_evidence(self, context: AuditContext) -> SafetyEvidence:
        if self.definition.request.mode is AuditMode.ARTIFACT:
            return self.provider.collect(self.definition.request, context)  # type: ignore[union-attr]

        with tempfile.TemporaryDirectory(prefix="proteus-safety-evidence-") as directory:
            snapshot_root = Path(directory) / "snapshot"
            shutil.copytree(context.snapshot_root, snapshot_root)
            replay_context = replace(context, snapshot_root=snapshot_root)
            return self.provider.collect(self.definition.request, replay_context)  # type: ignore[union-attr]

    def _assessment(self, evidence: SafetyEvidence) -> AuditAssessment:
        if evidence.mode is not self.definition.request.mode:
            return self._invalid(evidence, "provider evidence mode does not match the request")

        try:
            validate_evidence_refs(evidence.evidence_refs)
        except ValueError:
            return self._invalid(evidence, "provider evidence references are invalid")

        invariant = evidence.observation.safety_invariant_violated
        if not evidence.evaluable:
            if invariant is not None:
                return self._invalid(
                    evidence,
                    "unevaluable evidence cannot carry a determinate safety invariant",
                )
            reason = evidence.reason.strip()
            if not reason:
                return self._invalid(evidence, "unevaluable evidence requires a reason")
            return AuditAssessment(
                status=AuditStatus.NOT_EVALUATED,
                exposure=evidence.exposure,
                observed_behavior=(*evidence.observed_behavior, reason),
                evidence_refs=evidence.evidence_refs,
                observation=evidence.observation,
            )

        if not evidence.evidence_refs:
            return self._invalid(evidence, "evaluable evidence requires evidence references")
        if invariant is None:
            return self._invalid(
                evidence,
                "evaluable evidence requires a determinate safety invariant",
            )
        if evidence.exposure is Exposure.NOT_EXPOSED:
            if invariant:
                return self._invalid(
                    evidence,
                    "not-exposed evidence cannot report a violated safety invariant",
                )
            return AuditAssessment(
                status=AuditStatus.NOT_EVALUATED,
                exposure=evidence.exposure,
                observed_behavior=evidence.observed_behavior,
                evidence_refs=evidence.evidence_refs,
                observation=evidence.observation,
            )
        if invariant:
            return AuditAssessment(
                status=AuditStatus.FAIL,
                exposure=evidence.exposure,
                observed_behavior=evidence.observed_behavior,
                failures=(self.definition.failure,),
                evidence_refs=evidence.evidence_refs,
                observation=evidence.observation,
            )
        return AuditAssessment(
            status=AuditStatus.PASS,
            exposure=evidence.exposure,
            observed_behavior=evidence.observed_behavior,
            evidence_refs=evidence.evidence_refs,
            observation=evidence.observation,
        )

    @staticmethod
    def _invalid(evidence: SafetyEvidence, message: str) -> AuditAssessment:
        try:
            validate_evidence_refs(evidence.evidence_refs)
        except ValueError:
            evidence_refs: tuple[str, ...] = ()
        else:
            evidence_refs = evidence.evidence_refs
        return AuditAssessment(
            status=AuditStatus.INVALID,
            exposure=evidence.exposure,
            observed_behavior=(*evidence.observed_behavior, message),
            evidence_refs=evidence_refs,
            observation=evidence.observation,
        )


@dataclass(frozen=True)
class SafetyMeasurementEvaluator:
    """A suite that resolves one generic evidence provider for all definitions."""

    name: str
    version: str
    definitions: tuple[SafetyMeasurementDefinition, ...]
    provider: SafetyEvidenceProvider | None = None

    def cases(
        self, adapter: HarnessAdapter, surfaces: Sequence[Surface]
    ) -> tuple[AuditCase, ...]:
        del surfaces
        provider = self.provider
        if provider is None and isinstance(adapter, SafetyEvidenceAdapter):
            provider = adapter.safety_evidence_provider()
        return tuple(
            SafetyMeasurementCase(definition=definition, provider=provider)
            for definition in self.definitions
        )
