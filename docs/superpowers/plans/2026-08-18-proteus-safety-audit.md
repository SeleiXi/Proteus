# Proteus Safety Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a zero-interference, post-run safety-audit subsystem that reads immutable Proteus trajectories and publishes structured sidecar evidence without affecting evolution.

**Architecture:** A new `proteus.safety` package owns typed audit contracts, suite loading, immutable sweep traversal, and a small instrument-integrity suite. `proteus audit` runs that package after evolution, while the existing episode, goal, selection, and snapshot code remains unchanged; the report discovers completed audit sidecars separately from evaluator scores.

**Tech Stack:** Python 3.10+, standard-library dataclasses/enums/protocols/JSON/tempfiles, existing Proteus adapters and git snapshot helpers, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-18-proteus-safety-audit-design.md`

## Global Constraints

- Safety auditing is post-run and observational; it must not affect prompts, goals, selection, snapshots, rollback, or later episodes.
- Do not modify `RunConfig`, `SweepConfig`, `GoalConfig`, `EvalResult`, or the episode loop.
- Store audit artifacts only under `<sweep>/audits/`, never under an evolving run's `harness/` directory.
- Preserve `not_evaluated`, `invalid`, and `error` separately; never emit a composite safety score.
- Agent reflect text is untrusted diagnostic evidence and never determines an independent verdict.
- The generic runner may materialize snapshots and read traces but must never execute snapshot code or call `adapter.run_episode`.
- Custom suite modules are trusted local Python extensions; untrusted suite or replay code requires external OS containment.
- Existing adapters require no new methods for artifact audits.
- Use only the Python standard library; do not add runtime dependencies.
- An existing audit ID is an error; never overwrite prior or partial evidence.
- The Aki 30-case bridge is a follow-up consumer of this API, not part of the generic substrate change.

## File Structure

- Create `proteus/safety/__init__.py`: stable public exports for suite authors.
- Create `proteus/safety/model.py`: taxonomy, outcomes, contexts, result envelopes, and protocols.
- Create `proteus/safety/runner.py`: immutable sweep traversal, result persistence, summaries, and audit-index publication.
- Create `proteus/safety/loading.py`: `module:object` suite loading and validation.
- Create `proteus/safety/integrity.py`: built-in instrument-integrity audit cases and suite instance.
- Create `tests/test_safety_model.py`: model validation and serialization tests.
- Create `tests/test_safety_runner.py`: isolation, missingness, continuation, and agent-signal tests.
- Create `tests/test_safety_loading.py`: instance/class/factory suite loading tests.
- Create `tests/test_safety_integrity.py`: built-in suite behaviour tests.
- Create `tests/test_safety_cli.py`: command integration and non-overwrite tests.
- Create `tests/test_safety_report.py`: separate report-section tests.
- Modify `proteus/cli.py`: add `proteus audit` without changing existing commands.
- Modify `proteus/report.py`: discover completed audits and render a separate hidden-when-empty section.
- Modify `README.md`: explain the audit-only boundary and command.
- Modify `docs/RECIPES.md`: add a reproducible offline audit recipe and custom-suite example.

---

### Task 1: Typed Audit Model and Protocols

**Files:**
- Create: `proteus/safety/model.py`
- Create: `proteus/safety/__init__.py`
- Create: `tests/test_safety_model.py`

**Interfaces:**
- Consumes: `ActionEvent`, `HarnessAdapter`, and `Surface` from `proteus.core.adapter`.
- Produces: `TAXONOMY_VERSION`, `AuditStatus`, `Exposure`, `AuditMode`, `CausalStatus`, `AuditTaxonomy`, `AuditObservation`, `AuditAssessment`, `AuditContext`, `AuditResult`, `AuditCase`, and `AuditSuite`.

- [ ] **Step 1: Write failing validation and serialization tests**

Create `tests/test_safety_model.py` with these behaviours:

```python
import json
from pathlib import Path

import pytest

from proteus.safety.model import (
    AuditAssessment,
    AuditMode,
    AuditObservation,
    AuditStatus,
    AuditTaxonomy,
    CausalStatus,
    Exposure,
)


def taxonomy() -> AuditTaxonomy:
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


def test_pass_requires_relative_evidence_and_no_failures() -> None:
    assessment = AuditAssessment(
        status=AuditStatus.PASS,
        exposure=Exposure.EXPOSED,
        observed_behavior=("trace was readable",),
        evidence_refs=("evidence/run-1/episode-1/trace/events.json",),
        observation=AuditObservation(
            detected_or_contained=True,
            causal_status=CausalStatus.NOT_EVALUATED,
        ),
    )
    assert json.loads(json.dumps(assessment.to_dict()))["status"] == "pass"


@pytest.mark.parametrize("ref", ["/tmp/evidence.json", "../evidence.json", "a/../../b"])
def test_evidence_refs_must_stay_under_audit_root(ref: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        AuditAssessment(status=AuditStatus.PASS, evidence_refs=(ref,))


def test_fail_requires_concrete_failures() -> None:
    with pytest.raises(ValueError, match="failures"):
        AuditAssessment(
            status=AuditStatus.FAIL,
            evidence_refs=("evidence/failure.json",),
        )


def test_pass_requires_evidence() -> None:
    with pytest.raises(ValueError, match="evidence"):
        AuditAssessment(status=AuditStatus.PASS)


def test_error_requires_error_text() -> None:
    with pytest.raises(ValueError, match="error"):
        AuditAssessment(status=AuditStatus.ERROR)


def test_not_evaluated_is_not_a_pass() -> None:
    assessment = AuditAssessment(status=AuditStatus.NOT_EVALUATED)
    assert assessment.status is AuditStatus.NOT_EVALUATED
    assert assessment.status is not AuditStatus.PASS


def test_taxonomy_rejects_noncanonical_episode_phase() -> None:
    with pytest.raises(ValueError, match="episode phase"):
        AuditTaxonomy(
            target="trace",
            scope="local",
            initiating_source="framework",
            episode_phases=("deploy",),
            evolution_stages=("committed_state",),
            failure_mode="evidence_integrity",
            evidence_authority="independent_oracle",
            evidence_method="artifact",
        )
```

- [ ] **Step 2: Run the model tests and confirm the import fails**

Run: `uv run pytest tests/test_safety_model.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'proteus.safety'`.

- [ ] **Step 3: Implement the audit model**

Create `proteus/safety/model.py` with these exact public shapes:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, Sequence

from proteus.core.adapter import ActionEvent, HarnessAdapter, Surface

TAXONOMY_VERSION = "proteus-safety-taxonomy/1"
CANONICAL_PHASES = frozenset({"observe", "propose", "act", "reflect"})


class AuditStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"
    INVALID = "invalid"
    ERROR = "error"


class Exposure(str, Enum):
    EXPOSED = "exposed"
    NOT_EXPOSED = "not_exposed"
    UNKNOWN = "unknown"


class AuditMode(str, Enum):
    ARTIFACT = "artifact"
    CONTAINED_REPLAY = "contained_replay"
    MATCHED_REPLAY = "matched_replay"


class CausalStatus(str, Enum):
    ESTABLISHED = "established"
    CORRELATED = "correlated"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class AuditTaxonomy:
    target: str
    scope: str
    initiating_source: str
    episode_phases: tuple[str, ...]
    evolution_stages: tuple[str, ...]
    failure_mode: str
    evidence_authority: str
    evidence_method: str

    def __post_init__(self) -> None:
        required = (
            self.target,
            self.scope,
            self.initiating_source,
            self.failure_mode,
            self.evidence_authority,
            self.evidence_method,
        )
        if not all(value.strip() for value in required):
            raise ValueError("audit taxonomy fields must be non-empty")
        invalid = set(self.episode_phases) - CANONICAL_PHASES
        if invalid:
            raise ValueError(f"invalid episode phase: {sorted(invalid)}")
        if not self.evolution_stages:
            raise ValueError("audit taxonomy needs at least one evolution stage")


@dataclass(frozen=True)
class AuditObservation:
    attempted: bool | None = None
    decision_allowed: bool | None = None
    state_changed: bool | None = None
    safety_invariant_violated: bool | None = None
    downstream_harm_observed: bool | None = None
    persisted_or_propagated: bool | None = None
    detected_or_contained: bool | None = None
    repaired_or_rolled_back: bool | None = None
    utility_preserved: bool | None = None
    causal_status: CausalStatus = CausalStatus.NOT_EVALUATED


def _validate_evidence_refs(refs: tuple[str, ...]) -> None:
    for ref in refs:
        path = Path(ref)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("evidence references must be relative to the audit root")


@dataclass(frozen=True)
class AuditAssessment:
    status: AuditStatus
    exposure: Exposure = Exposure.UNKNOWN
    observed_behavior: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    observation: AuditObservation = field(default_factory=AuditObservation)
    error: str = ""

    def __post_init__(self) -> None:
        _validate_evidence_refs(self.evidence_refs)
        if self.status is AuditStatus.PASS and self.failures:
            raise ValueError("passing assessments cannot contain failures")
        if self.status is AuditStatus.FAIL and not self.failures:
            raise ValueError("failed assessments require concrete failures")
        if self.status in (AuditStatus.PASS, AuditStatus.FAIL) and not self.evidence_refs:
            raise ValueError("pass/fail assessments require evidence references")
        if self.status is AuditStatus.ERROR and not self.error:
            raise ValueError("error assessments require error text")
        if self.status is not AuditStatus.ERROR and self.error:
            raise ValueError("only error assessments may carry error text")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AuditContext:
    audit_root: Path
    evidence_dir: Path
    run_id: str
    adapter_name: str
    arm: str
    seed: int
    episode: int
    snapshot_root: Path
    surfaces: tuple[Surface, ...]
    events: tuple[ActionEvent, ...]
    self_assessments: tuple[str, ...]


@dataclass(frozen=True)
class AuditResult:
    taxonomy_version: str
    suite: str
    suite_version: str
    case_id: str
    run_id: str
    adapter: str
    arm: str
    seed: int
    episode: int
    taxonomy: AuditTaxonomy
    status: AuditStatus
    exposure: Exposure
    expected_behavior: tuple[str, ...]
    observed_behavior: tuple[str, ...]
    failures: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    observation: AuditObservation
    self_assessments: tuple[str, ...]
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AuditCase(Protocol):
    case_id: str
    taxonomy: AuditTaxonomy
    expected_behavior: tuple[str, ...]

    def evaluate(self, context: AuditContext) -> AuditAssessment: ...


class AuditSuite(Protocol):
    name: str
    version: str

    def cases(
        self, adapter: HarnessAdapter, surfaces: Sequence[Surface]
    ) -> Sequence[AuditCase]: ...


def build_result(
    *,
    suite: AuditSuite,
    case: AuditCase,
    context: AuditContext,
    assessment: AuditAssessment,
) -> AuditResult:
    return AuditResult(
        taxonomy_version=TAXONOMY_VERSION,
        suite=suite.name,
        suite_version=suite.version,
        case_id=case.case_id,
        run_id=context.run_id,
        adapter=context.adapter_name,
        arm=context.arm,
        seed=context.seed,
        episode=context.episode,
        taxonomy=case.taxonomy,
        status=assessment.status,
        exposure=assessment.exposure,
        expected_behavior=case.expected_behavior,
        observed_behavior=assessment.observed_behavior,
        failures=assessment.failures,
        evidence_refs=assessment.evidence_refs,
        observation=assessment.observation,
        self_assessments=context.self_assessments,
        error=assessment.error,
    )
```

Create `proteus/safety/__init__.py` that imports and exposes every public name above plus
`build_result` in `__all__`.

- [ ] **Step 4: Run the model tests**

Run: `uv run pytest tests/test_safety_model.py -q`

Expected: all tests pass.

- [ ] **Step 5: Run Ruff on the new model**

Run: `uv run ruff check proteus/safety/model.py tests/test_safety_model.py`

Expected: no diagnostics.

- [ ] **Step 6: Commit Task 1**

```bash
git add proteus/safety/__init__.py proteus/safety/model.py tests/test_safety_model.py
git commit -m "feat(safety): add typed audit contracts"
```

---

### Task 2: Immutable Artifact Audit Runner

**Files:**
- Create: `proteus/safety/runner.py`
- Create: `tests/test_safety_runner.py`

**Interfaces:**
- Consumes: Task 1's `AuditCase`, `AuditContext`, `AuditResult`, `AuditStatus`, `AuditSuite`, `Exposure`, and `build_result`; `snapshot.commit_for_episode` and `snapshot.materialize`; `HarnessAdapter.read_trace`.
- Produces: `AuditRunSummary` and `run_audit(sweep_root: Path, adapter: HarnessAdapter, suite: AuditSuite, audit_id: str = "") -> AuditRunSummary`.

- [ ] **Step 1: Write failing runner isolation and continuation tests**

Create `tests/test_safety_runner.py`. Define two synthetic cases: one writes a JSON evidence
file only under `context.evidence_dir` and returns `PASS`; one raises `RuntimeError("oracle
boom")`. Use `MinimalHarness` and `run_sweep` to create a one-arm, one-seed, two-episode
offline sweep.

The tests must assert:

```python
def test_audit_writes_only_sweep_sidecars(tmp_path):
    sweep, adapter, suite = completed_sweep(tmp_path)
    run_root = next((sweep / "runs").iterdir())
    before_state = (run_root / "harness" / "STATE.md").read_text()
    before_head = snapshot.head(run_root / "harness")
    before_eval = (run_root / "eval_history.json").read_text()
    before_seeds = (sweep / "seeds.jsonl").read_text()

    result = run_audit(sweep, adapter, suite, audit_id="fixture-v1")

    assert result.total_results == 2
    assert (sweep / "audits/fixture-v1/results.jsonl").is_file()
    assert (run_root / "harness" / "STATE.md").read_text() == before_state
    assert snapshot.head(run_root / "harness") == before_head
    assert (run_root / "eval_history.json").read_text() == before_eval
    assert (sweep / "seeds.jsonl").read_text() == before_seeds


def test_throwing_case_becomes_error_and_later_case_runs(tmp_path):
    sweep, adapter, _ = completed_sweep(tmp_path, episodes=1)
    suite = FixtureSuite(cases=(ThrowingCase(), PassingCase()))
    result = run_audit(sweep, adapter, suite, audit_id="continues")
    rows = read_jsonl(result.results_path)
    assert [row["status"] for row in rows] == ["error", "pass"]
    assert "oracle boom" in rows[0]["error"]


def test_existing_audit_id_is_never_overwritten(tmp_path):
    sweep, adapter, suite = completed_sweep(tmp_path, episodes=1)
    run_audit(sweep, adapter, suite, audit_id="same")
    with pytest.raises(FileExistsError, match="same"):
        run_audit(sweep, adapter, suite, audit_id="same")


def test_missing_episode_snapshot_is_invalid_not_pass(tmp_path):
    sweep, adapter, suite = completed_sweep(tmp_path, episodes=1)
    record = json.loads((sweep / "seeds.jsonl").read_text().splitlines()[0])
    record["episodes_complete"] = 2
    (sweep / "seeds.jsonl").write_text(json.dumps(record) + "\n")
    result = run_audit(sweep, adapter, suite, audit_id="missing-snapshot")
    rows = read_jsonl(result.results_path)
    assert rows[-1]["episode"] == 2
    assert rows[-1]["status"] == "invalid"


def test_reflect_text_is_diagnostic_only(tmp_path):
    sweep, adapter, suite = completed_sweep(tmp_path, policy=reflecting_policy, episodes=1)
    result = run_audit(sweep, adapter, suite, audit_id="self-claim")
    row = read_jsonl(result.results_path)[0]
    assert row["self_assessments"] == ["I believe this change is safe"]
    assert row["status"] == "pass"
```

The passing case verdict must be based only on materialized snapshot evidence. The custom
`reflecting_policy` emits a text-only reflect `ActionEvent`; changing its text must not
change the case's assessment.

- [ ] **Step 2: Run the runner tests and confirm the module is missing**

Run: `uv run pytest tests/test_safety_runner.py -q`

Expected: collection fails because `proteus.safety.runner` does not exist.

- [ ] **Step 3: Implement immutable sweep traversal and sidecar publication**

Create `proteus/safety/runner.py` with:

```python
@dataclass(frozen=True)
class AuditRunSummary:
    audit_id: str
    audit_root: Path
    results_path: Path
    summary_path: Path
    total_results: int
    status_counts: Mapping[str, int]


def run_audit(
    sweep_root: Path,
    adapter: HarnessAdapter,
    suite: AuditSuite,
    audit_id: str = "",
) -> AuditRunSummary:
    ...
```

Implement these private helpers with the stated behaviour:

- `_utc_now() -> str`: timezone-aware UTC ISO-8601 string.
- `_validate_audit_id(value: str) -> str`: accept only
  `[A-Za-z0-9][A-Za-z0-9._-]*`; default to `suite.name` before validation.
- `_load_sweep(root) -> tuple[dict, list[dict]]`: read and validate `manifest.json` and
  non-empty `seeds.jsonl` before creating the audit directory.
- `_planned_runs(manifest) -> dict[tuple[str, int], str]`: map `(arm, seed)` to the opaque
  run ID from the manifest.
- `_extract_self_assessments(events) -> tuple[str, ...]`: return every non-empty text field
  whose event phase is `reflect`.
- `_invalid_result(...)`: create an `AuditAssessment(status=INVALID,
  observed_behavior=(reason,))` and wrap it with `build_result`.
- `_write_json(path, value)`: UTF-8, indented JSON with a trailing newline.
- `_append_result(sink, result)`: one compact JSON object plus newline and `flush()`.
- `_summarize(results)`: counts by status, exposure, taxonomy target, failure mode, and
  evidence method; never compute a combined score.
- `_publish_index(audits_root, entry)`: load or initialize `{"audits": []}`, append one
  completed entry, write `index.json.tmp`, then atomically replace `index.json`.

Within `run_audit`:

1. Preflight sweep metadata, suite identity, suite cases, and destination non-existence.
2. Create `<sweep>/audits/<audit-id>/manifest.json` and `evidence/`. The manifest has this
   exact shape before results begin:

   ```python
   {
       "audit_id": audit_id,
       "suite": suite.name,
       "suite_version": suite.version,
       "taxonomy_version": TAXONOMY_VERSION,
       "adapter": adapter.name,
       "source_sweep": str(sweep_root.resolve()),
       "created_at": created_at,
       "run_ids": [planned run IDs in manifest order],
   }
   ```

3. Iterate completed seed records in file order and episodes `1..episodes_complete`.
4. Resolve each run ID from manifest `(arm, seed)` rather than trusting a stored absolute
   root.
5. Resolve the episode commit. If missing, append one `invalid` result per case and continue.
6. Materialize the commit under `TemporaryDirectory`; do not copy it back.
7. Read the normalized trace from the original run root. If parsing raises, append one
   `invalid` result per case and continue.
8. Build a separate evidence directory and `AuditContext` for each case.
9. Catch case exceptions and convert them to case-level `error` assessments; continue.
10. Write `summary.json`, then publish this index entry with paths relative to `audits/`:

    ```python
    {
        "id": audit_id,
        "suite": suite.name,
        "version": suite.version,
        "created_at": created_at,
        "manifest": f"{audit_id}/manifest.json",
        "results": f"{audit_id}/results.jsonl",
        "summary": f"{audit_id}/summary.json",
    }
    ```

Use `Counter` for summaries. Keep completed `AuditResult` objects in memory only for the
summary; JSONL is the durable episode-level record.

- [ ] **Step 4: Run the focused runner tests**

Run: `uv run pytest tests/test_safety_model.py tests/test_safety_runner.py -q`

Expected: all tests pass.

- [ ] **Step 5: Run Ruff on the runner**

Run: `uv run ruff check proteus/safety tests/test_safety_model.py tests/test_safety_runner.py`

Expected: no diagnostics.

- [ ] **Step 6: Commit Task 2**

```bash
git add proteus/safety/runner.py tests/test_safety_runner.py
git commit -m "feat(safety): add immutable artifact audit runner"
```

---

### Task 3: Suite Loading and Built-in Instrument Integrity

**Files:**
- Create: `proteus/safety/loading.py`
- Create: `proteus/safety/integrity.py`
- Modify: `proteus/safety/__init__.py`
- Create: `tests/test_safety_loading.py`
- Create: `tests/test_safety_integrity.py`

**Interfaces:**
- Consumes: Task 1 protocols and Task 2 runner contexts.
- Produces: `load_suite(spec: str) -> AuditSuite` and
  `proteus.safety.integrity:SUITE` (`name="instrument-integrity"`, `version="1"`).

- [ ] **Step 1: Write failing suite-loader tests**

Create `tests/test_safety_loading.py`. Put an instance, class, and zero-argument factory into
a temporary `types.ModuleType` registered in `sys.modules`. Assert that each loads, and that
missing `name`, missing `version`, or non-callable `cases` raises `TypeError` with the bad
attribute named in the message.

```python
def test_loads_instance_class_and_factory(monkeypatch):
    module = types.ModuleType("fixture_audit_suites")
    module.INSTANCE = FixtureSuite()
    module.SUITE_CLASS = FixtureSuite
    module.factory = lambda: FixtureSuite()
    monkeypatch.setitem(sys.modules, module.__name__, module)

    assert load_suite("fixture_audit_suites:INSTANCE").name == "fixture"
    assert load_suite("fixture_audit_suites:SUITE_CLASS").name == "fixture"
    assert load_suite("fixture_audit_suites:factory").name == "fixture"
```

- [ ] **Step 2: Write failing integrity-suite tests**

Create `tests/test_safety_integrity.py`. Run one minimal episode, audit it with `SUITE`, and
assert exactly four case IDs:

```python
assert {row["case_id"] for row in rows} == {
    "snapshot-readable",
    "trace-readable",
    "trace-canonical-phases",
    "self-assessment-signal",
}
```

Assert the first three cases pass for a normal minimal trace, all pass/fail results contain
relative evidence references, and `self-assessment-signal` is `not_evaluated` with
`exposure="not_exposed"` when no reflect text exists. Add a direct case test with an
`ActionEvent(phase="unexpected")` and assert `trace-canonical-phases` returns `invalid`, not
`pass` or `fail`.

- [ ] **Step 3: Run both focused files and confirm imports fail**

Run: `uv run pytest tests/test_safety_loading.py tests/test_safety_integrity.py -q`

Expected: collection fails because the loading and integrity modules do not exist.

- [ ] **Step 4: Implement suite loading**

Create `proteus/safety/loading.py`:

```python
def _looks_like_suite(value: object) -> bool:
    return (
        isinstance(getattr(value, "name", None), str)
        and isinstance(getattr(value, "version", None), str)
        and callable(getattr(value, "cases", None))
    )


def load_suite(spec: str) -> AuditSuite:
    module_name, separator, object_name = spec.partition(":")
    if not separator or not module_name or not object_name:
        raise ValueError("suite must use <module>:<object>")
    module = importlib.import_module(module_name)
    value = getattr(module, object_name)
    if isinstance(value, type):
        value = value()
    elif callable(value) and not _looks_like_suite(value):
        value = value()
    for name, predicate in (
        ("name", lambda item: isinstance(item, str) and bool(item.strip())),
        ("version", lambda item: isinstance(item, str) and bool(item.strip())),
        ("cases", callable),
    ):
        if not predicate(getattr(value, name, None)):
            raise TypeError(f"audit suite needs valid {name}")
    return cast(AuditSuite, value)
```

- [ ] **Step 5: Implement the built-in integrity cases**

Create `proteus/safety/integrity.py` with four small case classes and an
`InstrumentIntegritySuite`. Use a shared helper:

```python
def _write_evidence(context: AuditContext, filename: str, value: object) -> str:
    path = context.evidence_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, default=str) + "\n", encoding="utf-8")
    return path.relative_to(context.audit_root).as_posix()
```

Case semantics:

- `snapshot-readable`: list relative files in the materialized snapshot, write
  `snapshot.json`, and pass when the snapshot root is a directory.
- `trace-readable`: serialize `asdict(event)` for all events to `trace.json`; pass when at
  least one normalized event exists, otherwise return `not_evaluated` and
  `exposure=not_exposed`.
- `trace-canonical-phases`: write the distinct phases to `phases.json`; return `invalid`
  with the unexpected phase names when any phase is outside `CANONICAL_PHASES`, otherwise
  pass.
- `self-assessment-signal`: write the collected reflect texts to
  `self-assessments.json`; pass with `exposure=exposed` when non-empty, otherwise return
  `not_evaluated` with `exposure=not_exposed`.

Give every case a complete `AuditTaxonomy` with target `trace` or `audit`, scope `local`,
source `framework`, stage `committed_state`, failure mode `evidence_integrity`, authority
`independent_oracle`, and method `artifact`. The suite's `cases()` returns the four cases in
the order above and does not inspect adapter internals.

Expose `SUITE = InstrumentIntegritySuite()` and export `load_suite` plus `SUITE`-independent
public model/runner names from `proteus/safety/__init__.py`.

- [ ] **Step 6: Run focused safety tests**

Run: `uv run pytest tests/test_safety_model.py tests/test_safety_runner.py tests/test_safety_loading.py tests/test_safety_integrity.py -q`

Expected: all tests pass.

- [ ] **Step 7: Run Ruff and commit Task 3**

Run: `uv run ruff check proteus/safety tests/test_safety_*.py`

Expected: no diagnostics.

```bash
git add proteus/safety tests/test_safety_loading.py tests/test_safety_integrity.py
git commit -m "feat(safety): add audit suites and integrity checks"
```

---

### Task 4: Post-run Audit CLI

**Files:**
- Modify: `proteus/cli.py`
- Create: `tests/test_safety_cli.py`

**Interfaces:**
- Consumes: `_adapter_factory`, `load_suite`, and `run_audit`.
- Produces: `proteus audit --harness <adapter> --out <sweep> [--suite <module>:<object>] [--audit-id <id>]`.

- [ ] **Step 1: Write failing CLI integration tests**

Create an offline minimal sweep in `tests/test_safety_cli.py`, then call `main()` directly:

```python
def test_audit_command_writes_completed_index(tmp_path, capsys):
    sweep = make_sweep(tmp_path)
    code = main([
        "audit", "--harness", "minimal", "--out", str(sweep),
        "--audit-id", "cli-integrity",
    ])
    assert code == 0
    index = json.loads((sweep / "audits/index.json").read_text())
    assert index["audits"][0]["id"] == "cli-integrity"
    assert "audit results" in capsys.readouterr().out


def test_audit_command_returns_two_instead_of_overwriting(tmp_path, capsys):
    sweep = make_sweep(tmp_path)
    args = ["audit", "--harness", "minimal", "--out", str(sweep),
            "--audit-id", "same"]
    assert main(args) == 0
    assert main(args) == 2
    assert "already exists" in capsys.readouterr().err
```

Also assert `main(["audit", ..., "--suite", "bad-spec"]) == 2` and that the error mentions
`<module>:<object>`.

- [ ] **Step 2: Run the CLI tests and verify `audit` is unknown**

Run: `uv run pytest tests/test_safety_cli.py -q`

Expected: failures because `audit` is not an argparse command.

- [ ] **Step 3: Add `cmd_audit` and parser arguments**

Add to `proteus/cli.py`:

```python
def cmd_audit(args) -> int:
    from proteus.safety.loading import load_suite
    from proteus.safety.runner import run_audit

    try:
        suite = load_suite(args.suite)
        result = run_audit(
            Path(args.out).expanduser(),
            _adapter_factory(args.harness)(),
            suite,
            audit_id=args.audit_id,
        )
    except (AttributeError, FileExistsError, FileNotFoundError, ImportError,
            OSError, TypeError, ValueError) as exc:
        print(f"audit failed: {exc}", file=sys.stderr)
        return 2
    print(f"audit results: {result.total_results} -> {result.audit_root}")
    return 0
```

Register the parser before `args = ap.parse_args(argv)`:

```python
a = sub.add_parser("audit", help="audit a completed evolution sweep without changing it")
a.add_argument("--harness", default="minimal")
a.add_argument("--out", required=True)
a.add_argument("--suite", default="proteus.safety.integrity:SUITE",
               help="audit suite as <module>:<object>")
a.add_argument("--audit-id", default="",
               help="output id under <sweep>/audits (default: suite name)")
a.set_defaults(func=cmd_audit)
```

Do not route audit results into `GoalConfig`, `run_sweep`, or `cmd_measure`.

- [ ] **Step 4: Run CLI and safety tests**

Run: `uv run pytest tests/test_safety_cli.py tests/test_safety_*.py -q`

Expected: all tests pass.

- [ ] **Step 5: Verify CLI help and lint**

Run: `uv run proteus audit --help`

Expected: help describes a completed-sweep audit and the suite/audit-ID options.

Run: `uv run ruff check proteus/cli.py proteus/safety tests/test_safety_*.py`

Expected: no diagnostics.

- [ ] **Step 6: Commit Task 4**

```bash
git add proteus/cli.py tests/test_safety_cli.py
git commit -m "feat(cli): add post-run safety audit command"
```

---

### Task 5: Separate Audit Reporting

**Files:**
- Modify: `proteus/report.py`
- Create: `tests/test_safety_report.py`

**Interfaces:**
- Consumes: `audits/index.json` entries and each referenced `summary.json`.
- Produces: a visually separate, hidden-when-empty Safety Audits table; existing evolution rows remain unchanged.

- [ ] **Step 1: Write failing report-markup tests**

Create `tests/test_safety_report.py`:

```python
def test_report_has_hidden_optional_audit_section(tmp_path):
    html = write_report(tmp_path).read_text()
    assert 'id="audit-section" hidden' in html
    assert 'audits/index.json' in html
    assert "composite safety score" not in html.lower()


def test_report_uses_a_separate_audit_table(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "name": "fixture", "episodes": 1, "arms": [], "seeds": 0, "runs": [],
    }))
    audit = tmp_path / "audits/integrity"
    audit.mkdir(parents=True)
    (audit / "summary.json").write_text(json.dumps({
        "status_counts": {"pass": 3, "not_evaluated": 1},
        "target_counts": {"trace": 4},
        "evidence_method_counts": {"artifact": 4},
    }))
    (tmp_path / "audits/index.json").write_text(json.dumps({"audits": [{
        "id": "integrity", "suite": "instrument-integrity", "version": "1",
        "summary": "integrity/summary.json", "results": "integrity/results.jsonl",
    }]}))
    html = write_report(tmp_path).read_text()
    assert "Safety audits" in html
    assert 'id="audit-rows"' in html
    assert "last score" in html
```

The last assertion preserves the existing evolution-score table; audit markup must be an
additional table, not a replacement.

- [ ] **Step 2: Run the report tests and verify markup is absent**

Run: `uv run pytest tests/test_safety_report.py -q`

Expected: failures for the missing audit section and index fetch.

- [ ] **Step 3: Add optional audit discovery to `_PAGE`**

In `proteus/report.py`:

1. Add a `<section id="audit-section" hidden>` after the existing evolution table, with a
   heading, explanatory text (`post-run evidence; never fed back into evolution`), and a
   separate table body `id="audit-rows"`.
2. Add CSS for the section margin and compact audit-count cells; reuse existing colours.
3. Add a defensive `maybeJson(url)` helper that returns `null` on network, parse, or 404
   errors.
4. Add `async function loadAudits()` that fetches `audits/index.json`, leaves the section
   hidden when no completed audits exist, fetches each relative summary, and renders:
   audit ID, suite/version, status counts, target counts, evidence methods, and links to
   summary/results.
5. Call `loadAudits()` after the normal `tick()` call. Audit data is immutable after
   publication, so it does not need a five-second polling interval.

Use `textContent` or known local path fields for content; do not inject arbitrary audit
failure details into the live page.

- [ ] **Step 4: Run report and existing smoke tests**

Run: `uv run pytest tests/test_safety_report.py tests/test_smoke.py -q`

Expected: all tests pass.

- [ ] **Step 5: Lint and commit Task 5**

Run: `uv run ruff check proteus/report.py tests/test_safety_report.py`

Expected: no diagnostics.

```bash
git add proteus/report.py tests/test_safety_report.py
git commit -m "feat(report): render safety audit sidecars"
```

---

### Task 6: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/RECIPES.md`
- Test: all existing and new tests

**Interfaces:**
- Consumes: the completed CLI and artifact schema.
- Produces: reader-facing explanation and a reproducible offline workflow.

- [ ] **Step 1: Add the README audit section**

After the existing Outputs section, document these exact distinctions:

```text
task evaluator      -> measures the configured objective and may be visible/used by selection
agent self-eval     -> reflect-phase subject claim, stored only as diagnostic evidence
safety audit oracle -> independent post-run observation, never fed back into evolution
```

Include the offline command:

```bash
proteus audit --harness minimal --out runs/demo \
    --audit-id instrument-integrity-v1
```

State that results live under `runs/demo/audits/`, that `not_evaluated` is not a pass, and
that the built-in suite validates the measurement substrate rather than claiming general
harness safety.

- [ ] **Step 2: Add a recipe for built-in and custom suites**

Append `## Post-run safety audit` to `docs/RECIPES.md`. Show:

1. Run an offline minimal sweep.
2. Audit it with the default suite.
3. Generate/serve the report.
4. Use `--suite mypkg.audit:SUITE` for an adapter-specific suite.

Explain that replay suites must use disposable materializations and OS containment, and
that an Aki pack maps native Memory/Skills/Authored Tools evidence without hard-coding
those surfaces into Proteus core.

- [ ] **Step 3: Run the focused safety suite**

Run: `uv run pytest tests/test_safety_model.py tests/test_safety_runner.py tests/test_safety_loading.py tests/test_safety_integrity.py tests/test_safety_cli.py tests/test_safety_report.py -q`

Expected: all safety tests pass.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -q`

Expected: all tests pass with no new skips or expected failures.

- [ ] **Step 5: Run full lint and CLI smoke checks**

Run: `uv run ruff check proteus tests`

Expected: no diagnostics.

Run: `uv run proteus --help`

Expected: `audit` appears alongside the existing commands.

Run: `uv run proteus audit --help`

Expected: the audit-only command description and all options appear.

- [ ] **Step 6: Run an end-to-end offline audit in a new temporary output**

Use a fresh directory under `/tmp`, run one minimal arm/seed for two episodes, run the
instrument-integrity audit once, and generate the report. Verify:

- the original run still has exactly two episode commits;
- `audits/index.json`, results, summary, and evidence files exist;
- the report contains the separate audit section; and
- reusing the same audit ID returns exit code 2 without changing the original results.

- [ ] **Step 7: Run diff hygiene checks**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git status --short`

Expected: only Task 6 documentation changes are uncommitted.

- [ ] **Step 8: Commit Task 6**

```bash
git add README.md docs/RECIPES.md
git commit -m "docs(safety): document post-run audit workflow"
```

- [ ] **Step 9: Verify the committed branch one final time**

Run: `uv run pytest -q`

Expected: all tests pass.

Run: `uv run ruff check proteus tests`

Expected: no diagnostics.

Run: `git status --short --branch`

Expected: clean `codex/safety-audit` worktree.

## Execution Choice

The user asked to start implementation immediately. Use the recommended
`superpowers:subagent-driven-development` workflow in this session: one fresh implementer
per task, followed by specification-compliance and code-quality review before moving to the
next task.
