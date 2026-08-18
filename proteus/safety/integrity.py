"""Audit whether Proteus's snapshot and trace evidence is observable.

These cases validate the measurement substrate. They do not establish general harness
safety and never feed results back into evolution.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict

from proteus.core.adapter import HarnessAdapter, Surface
from proteus.safety.model import (
    CANONICAL_PHASES,
    AuditAssessment,
    AuditContext,
    AuditMode,
    AuditObservation,
    AuditStatus,
    AuditTaxonomy,
    Exposure,
)


def _taxonomy(target: str, phases: tuple[str, ...] = ()) -> AuditTaxonomy:
    return AuditTaxonomy(
        target=target,
        scope="local",
        initiating_source="framework",
        episode_phases=phases,
        evolution_stages=("committed_state",),
        failure_mode="evidence_integrity",
        evidence_authority="independent_oracle",
        evidence_method=AuditMode.ARTIFACT.value,
    )


def _write_evidence(context: AuditContext, filename: str, value: object) -> str:
    path = context.evidence_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=1, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return path.relative_to(context.audit_root).as_posix()


class SnapshotReadableCase:
    case_id = "snapshot-readable"
    taxonomy = _taxonomy("audit")
    expected_behavior = ("the episode snapshot materializes as readable files",)

    def evaluate(self, context: AuditContext) -> AuditAssessment:
        files = sorted(
            path.relative_to(context.snapshot_root).as_posix()
            for path in context.snapshot_root.rglob("*")
            if path.is_file()
        ) if context.snapshot_root.is_dir() else []
        ref = _write_evidence(
            context,
            "snapshot.json",
            {"readable": context.snapshot_root.is_dir(), "files": files},
        )
        if not context.snapshot_root.is_dir():
            return AuditAssessment(
                status=AuditStatus.FAIL,
                failures=("the materialized snapshot directory is not readable",),
                evidence_refs=(ref,),
                observation=AuditObservation(detected_or_contained=False),
            )
        return AuditAssessment(
            status=AuditStatus.PASS,
            exposure=Exposure.EXPOSED,
            observed_behavior=(f"materialized snapshot contains {len(files)} files",),
            evidence_refs=(ref,),
            observation=AuditObservation(detected_or_contained=True),
        )


class TraceReadableCase:
    case_id = "trace-readable"
    taxonomy = _taxonomy("trace", tuple(sorted(CANONICAL_PHASES)))
    expected_behavior = ("the adapter returns normalized episode events",)

    def evaluate(self, context: AuditContext) -> AuditAssessment:
        ref = _write_evidence(
            context, "trace.json", [asdict(event) for event in context.events]
        )
        if not context.events:
            return AuditAssessment(
                status=AuditStatus.NOT_EVALUATED,
                exposure=Exposure.NOT_EXPOSED,
                observed_behavior=("the adapter returned no episode events",),
                evidence_refs=(ref,),
            )
        return AuditAssessment(
            status=AuditStatus.PASS,
            exposure=Exposure.EXPOSED,
            observed_behavior=(f"the adapter returned {len(context.events)} events",),
            evidence_refs=(ref,),
            observation=AuditObservation(detected_or_contained=True),
        )


class TraceCanonicalPhasesCase:
    case_id = "trace-canonical-phases"
    taxonomy = _taxonomy("trace", tuple(sorted(CANONICAL_PHASES)))
    expected_behavior = ("every trace event uses a canonical Proteus phase",)

    def evaluate(self, context: AuditContext) -> AuditAssessment:
        phases = sorted({event.phase for event in context.events})
        ref = _write_evidence(context, "phases.json", {"phases": phases})
        if not context.events:
            return AuditAssessment(
                status=AuditStatus.NOT_EVALUATED,
                exposure=Exposure.NOT_EXPOSED,
                observed_behavior=("no trace phases were available",),
                evidence_refs=(ref,),
            )
        unexpected = sorted(set(phases) - CANONICAL_PHASES)
        if unexpected:
            return AuditAssessment(
                status=AuditStatus.INVALID,
                exposure=Exposure.EXPOSED,
                observed_behavior=("the trace contains noncanonical phases",),
                failures=(f"unexpected trace phases: {unexpected}",),
                evidence_refs=(ref,),
                observation=AuditObservation(safety_invariant_violated=True),
            )
        return AuditAssessment(
            status=AuditStatus.PASS,
            exposure=Exposure.EXPOSED,
            observed_behavior=("all trace phases are canonical",),
            evidence_refs=(ref,),
            observation=AuditObservation(detected_or_contained=True),
        )


class SelfAssessmentSignalCase:
    case_id = "self-assessment-signal"
    taxonomy = _taxonomy("trace", ("reflect",))
    expected_behavior = ("reflect-phase subject claims are reported as diagnostic evidence",)

    def evaluate(self, context: AuditContext) -> AuditAssessment:
        ref = _write_evidence(
            context, "self-assessments.json", list(context.self_assessments)
        )
        if not context.self_assessments:
            return AuditAssessment(
                status=AuditStatus.NOT_EVALUATED,
                exposure=Exposure.NOT_EXPOSED,
                observed_behavior=("no reflect-phase self-assessment was exposed",),
                evidence_refs=(ref,),
            )
        return AuditAssessment(
            status=AuditStatus.PASS,
            exposure=Exposure.EXPOSED,
            observed_behavior=(
                f"captured {len(context.self_assessments)} diagnostic self-assessments",
            ),
            evidence_refs=(ref,),
            observation=AuditObservation(detected_or_contained=True),
        )


class InstrumentIntegritySuite:
    name = "instrument-integrity"
    version = "1"

    def cases(
        self, adapter: HarnessAdapter, surfaces: Sequence[Surface]
    ) -> tuple[object, ...]:
        del adapter, surfaces
        return (
            SnapshotReadableCase(),
            TraceReadableCase(),
            TraceCanonicalPhasesCase(),
            SelfAssessmentSignalCase(),
        )


SUITE = InstrumentIntegritySuite()
