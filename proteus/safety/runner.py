"""Read completed trajectories and publish independent audit sidecars."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from proteus.core import snapshot
from proteus.core.adapter import ActionEvent, HarnessAdapter
from proteus.safety.model import (
    TAXONOMY_VERSION,
    AuditAssessment,
    AuditCase,
    AuditContext,
    AuditObservation,
    AuditResult,
    AuditStatus,
    AuditSuite,
    Exposure,
    build_result,
)

_AUDIT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class AuditRunSummary:
    audit_id: str
    audit_root: Path
    results_path: Path
    summary_path: Path
    total_results: int
    status_counts: Mapping[str, int]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_component(value: object, label: str) -> str:
    if not isinstance(value, str) or not _AUDIT_ID.fullmatch(value):
        raise ValueError(f"{label} must match [A-Za-z0-9][A-Za-z0-9._-]*")
    return value


def _validate_audit_id(value: str) -> str:
    return _validate_component(value, "audit ID")


def _load_sweep(root: Path) -> tuple[dict, list[dict]]:
    manifest_path = root / "manifest.json"
    records_path = root / "seeds.jsonl"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    if not records_path.is_file():
        raise FileNotFoundError(records_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not isinstance(manifest.get("runs"), list):
        raise TypeError("sweep manifest needs a runs list")
    if not records:
        raise ValueError("completed sweep has no seed records")
    return manifest, records


def _planned_runs(manifest: dict) -> dict[tuple[str, int], str]:
    planned: dict[tuple[str, int], str] = {}
    for record in manifest["runs"]:
        try:
            key = (str(record["arm"]), int(record["seed"]))
            raw_run_id = record["id"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid planned run in sweep manifest") from exc
        run_id = _validate_component(raw_run_id, "run ID")
        if not run_id or key in planned:
            raise ValueError("sweep manifest has duplicate or empty run identity")
        planned[key] = run_id
    return planned


def _extract_self_assessments(events: Sequence[ActionEvent]) -> tuple[str, ...]:
    return tuple(
        event.text
        for event in events
        if event.phase == "reflect" and event.text.strip()
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=1, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _append_result(sink, result: AuditResult) -> None:
    sink.write(
        json.dumps(
            result.to_dict(), ensure_ascii=False, separators=(",", ":"), default=str
        )
        + "\n"
    )
    sink.flush()


def _result_without_context(
    *,
    suite: AuditSuite,
    case: AuditCase,
    run_id: str,
    adapter_name: str,
    arm: str,
    seed: int,
    episode: int,
    reason: str,
) -> AuditResult:
    return AuditResult(
        taxonomy_version=TAXONOMY_VERSION,
        suite=suite.name,
        suite_version=suite.version,
        case_id=case.case_id,
        run_id=run_id,
        adapter=adapter_name,
        arm=arm,
        seed=seed,
        episode=episode,
        taxonomy=case.taxonomy,
        status=AuditStatus.INVALID,
        exposure=Exposure.UNKNOWN,
        expected_behavior=case.expected_behavior,
        observed_behavior=(reason,),
        failures=(),
        evidence_refs=(),
        observation=AuditObservation(),
        self_assessments=(),
    )


def _summarize(results: Sequence[AuditResult]) -> dict[str, object]:
    def counts(values) -> dict[str, int]:
        return dict(sorted(Counter(values).items()))

    return {
        "total_results": len(results),
        "status_counts": counts(result.status.value for result in results),
        "exposure_counts": counts(result.exposure.value for result in results),
        "target_counts": counts(result.taxonomy.target for result in results),
        "failure_mode_counts": counts(
            result.taxonomy.failure_mode for result in results
        ),
        "evidence_method_counts": counts(
            result.taxonomy.evidence_method for result in results
        ),
    }


def _publish_index(audits_root: Path, entry: dict[str, object]) -> None:
    index_path = audits_root / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index.get("audits"), list):
            raise ValueError("audit index needs an audits list")
    else:
        index = {"audits": []}
    index["audits"].append(entry)
    temporary = audits_root / "index.json.tmp"
    _write_json(temporary, index)
    temporary.replace(index_path)


def run_audit(
    sweep_root: Path,
    adapter: HarnessAdapter,
    suite: AuditSuite,
    audit_id: str = "",
) -> AuditRunSummary:
    """Audit every completed episode without writing to the source trajectory."""
    sweep_root = Path(sweep_root)
    manifest, records = _load_sweep(sweep_root)
    planned = _planned_runs(manifest)
    run_ids = list(planned.values())

    if not isinstance(getattr(suite, "name", None), str) or not suite.name.strip():
        raise TypeError("audit suite needs a non-empty name")
    if not isinstance(getattr(suite, "version", None), str) or not suite.version.strip():
        raise TypeError("audit suite needs a non-empty version")

    surfaces = tuple(adapter.surfaces())
    cases = tuple(suite.cases(adapter, surfaces))
    if not cases:
        raise ValueError("audit suite has no cases")
    case_ids = [_validate_component(case.case_id, "case ID") for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("audit suite has duplicate case ID")

    for record in records:
        try:
            key = (str(record["arm"]), int(record["seed"]))
            int(record["episodes_complete"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid completed seed record") from exc
        if key not in planned:
            raise ValueError(f"completed seed record is not planned: {key}")

    audit_id = _validate_audit_id(audit_id or suite.name)
    audits_root = sweep_root / "audits"
    audit_root = audits_root / audit_id
    if audit_root.exists():
        raise FileExistsError(f"audit ID already exists: {audit_id}")

    created_at = _utc_now()
    audit_root.mkdir(parents=True)
    (audit_root / "evidence").mkdir()
    _write_json(
        audit_root / "manifest.json",
        {
            "audit_id": audit_id,
            "suite": suite.name,
            "suite_version": suite.version,
            "taxonomy_version": TAXONOMY_VERSION,
            "adapter": adapter.name,
            "source_sweep": str(sweep_root.resolve()),
            "created_at": created_at,
            "run_ids": run_ids,
        },
    )

    results: list[AuditResult] = []
    results_path = audit_root / "results.jsonl"
    with results_path.open("a", encoding="utf-8") as sink:
        for record in records:
            arm = str(record["arm"])
            seed = int(record["seed"])
            run_id = planned[(arm, seed)]
            run_root = sweep_root / "runs" / run_id
            work_tree = run_root / "harness"
            episodes = int(record["episodes_complete"])
            for episode in range(1, episodes + 1):
                try:
                    sha = snapshot.commit_for_episode(work_tree, episode)
                except (OSError, subprocess.SubprocessError) as exc:
                    sha = None
                    snapshot_error = f"snapshot lookup failed: {type(exc).__name__}: {exc}"
                else:
                    snapshot_error = f"episode {episode} snapshot is missing"
                if not sha:
                    for case in cases:
                        result = _result_without_context(
                            suite=suite,
                            case=case,
                            run_id=run_id,
                            adapter_name=adapter.name,
                            arm=arm,
                            seed=seed,
                            episode=episode,
                            reason=snapshot_error,
                        )
                        results.append(result)
                        _append_result(sink, result)
                    continue

                with tempfile.TemporaryDirectory(prefix="proteus-audit-") as temporary:
                    materialized = Path(temporary) / "harness"
                    try:
                        snapshot.materialize(work_tree, sha, materialized)
                    except (OSError, subprocess.SubprocessError) as exc:
                        reason = (
                            "snapshot materialization failed: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        for case in cases:
                            result = _result_without_context(
                                suite=suite,
                                case=case,
                                run_id=run_id,
                                adapter_name=adapter.name,
                                arm=arm,
                                seed=seed,
                                episode=episode,
                                reason=reason,
                            )
                            results.append(result)
                            _append_result(sink, result)
                        continue

                    try:
                        events = tuple(adapter.read_trace(run_root, episode))
                    except Exception as exc:  # noqa: BLE001 - adapter input becomes evidence
                        reason = f"trace parsing failed: {type(exc).__name__}: {exc}"
                        for case in cases:
                            result = _result_without_context(
                                suite=suite,
                                case=case,
                                run_id=run_id,
                                adapter_name=adapter.name,
                                arm=arm,
                                seed=seed,
                                episode=episode,
                                reason=reason,
                            )
                            results.append(result)
                            _append_result(sink, result)
                        continue

                    self_assessments = _extract_self_assessments(events)
                    for case in cases:
                        evidence_dir = (
                            audit_root
                            / "evidence"
                            / run_id
                            / f"episode-{episode}"
                            / case.case_id
                        )
                        evidence_dir.mkdir(parents=True, exist_ok=True)
                        context = AuditContext(
                            audit_root=audit_root,
                            evidence_dir=evidence_dir,
                            run_id=run_id,
                            adapter_name=adapter.name,
                            arm=arm,
                            seed=seed,
                            episode=episode,
                            run_root=run_root,
                            snapshot_root=materialized,
                            surfaces=surfaces,
                            events=events,
                            self_assessments=self_assessments,
                        )
                        try:
                            assessment = case.evaluate(context)
                            result = build_result(
                                suite=suite,
                                case=case,
                                context=context,
                                assessment=assessment,
                            )
                        except Exception as exc:  # noqa: BLE001 - isolate case failures
                            assessment = AuditAssessment(
                                status=AuditStatus.ERROR,
                                observed_behavior=("the audit case raised an exception",),
                                error=f"{type(exc).__name__}: {exc}",
                            )
                            result = build_result(
                                suite=suite,
                                case=case,
                                context=context,
                                assessment=assessment,
                            )
                        results.append(result)
                        _append_result(sink, result)

    summary = _summarize(results)
    summary_path = audit_root / "summary.json"
    _write_json(summary_path, summary)
    _publish_index(
        audits_root,
        {
            "id": audit_id,
            "suite": suite.name,
            "version": suite.version,
            "created_at": created_at,
            "manifest": f"{audit_id}/manifest.json",
            "results": f"{audit_id}/results.jsonl",
            "summary": f"{audit_id}/summary.json",
        },
    )
    status_counts = summary["status_counts"]
    assert isinstance(status_counts, dict)
    return AuditRunSummary(
        audit_id=audit_id,
        audit_root=audit_root,
        results_path=results_path,
        summary_path=summary_path,
        total_results=len(results),
        status_counts=status_counts,
    )
