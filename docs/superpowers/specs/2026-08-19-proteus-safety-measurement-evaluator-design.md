# Proteus Safety Measurement Evaluator Design

Date: 2026-08-19
Status: approved direction from conversation; implementation specification

## Summary

Proteus will own one generic safety measurement evaluator. It converts independent
evidence from a harness-neutral provider into the existing Proteus safety taxonomy,
outcome model, and audit sidecars.

Aki is not the package boundary. Aki snapshots are one validation input and an Aki
integration may supply one evidence provider, just as any other harness may. No Aki tool
names, surface names, trajectory rules, or replay commands belong in `proteus/safety/`.

The evaluator remains audit-only:

```text
completed trajectory snapshot
          |
          v
SafetyEvidenceProvider -- native observation / contained replay
          |
          v
SafetyMeasurementEvaluator -- independent verdict
          |
          v
<sweep>/audits/... sidecars and report

No edge returns to prompts, goals, selection, promotion, rollback, or later episodes.
```

## Existing Proteus Boundary

The existing safety-audit implementation already owns the generic infrastructure:

- `AuditTaxonomy` describes target, scope, source, lifecycle, failure mode, and evidence;
- `AuditStatus` preserves `pass`, `fail`, `not_evaluated`, `invalid`, and `error`;
- `Exposure` is separate from verdict;
- `AuditObservation` keeps nullable safety signals and causal status;
- `run_audit` reads completed sweeps, materializes immutable snapshots, isolates case
  failures, and publishes sidecars; and
- the CLI and report keep audit results separate from task scores.

The missing layer is a reusable way to ask a harness or external instrument for richer
safety evidence and convert it into an independent verdict. That layer must reuse the
existing runner rather than create an Aki runner inside Proteus core.

## Goals

1. Provide one generic evaluator for artifact, contained-replay, and matched-replay
   evidence.
2. Let a harness adapter or an explicitly injected provider supply native evidence.
3. Keep provider-native event formats outside the generic evaluator.
4. Preserve missingness: unavailable evidence becomes `not_evaluated`, never a pass.
5. Preserve invalidity: malformed or internally inconsistent evidence becomes `invalid`.
6. Keep exposure independent from outcome.
7. Give replay providers a disposable snapshot copy so one case cannot mutate another
   case's evidence.
8. Reuse the existing audit CLI, sidecar layout, report, and case-error isolation.

## Non-goals

- No safety gate, promotion decision, mutation approval, rollback, or agent feedback.
- No changes to `GoalConfig`, `EvalResult`, `RunConfig`, `SweepConfig`, or episode logic.
- No scalar safety score.
- No generic interpretation of Aki `post_tool_use`, permission hooks, model messages, or
  historical Git layout.
- No claim that Proteus itself contains provider code. A replay provider owns and must
  document its external OS containment boundary.
- No compatibility adapters for old evidence schemas.

## Public Model

`proteus/safety/model.py` adds four generic contracts.

### `SafetyEvidenceRequest`

One case's request to an evidence provider:

```python
@dataclass(frozen=True)
class SafetyEvidenceRequest:
    mode: AuditMode
    scenario: str
    parameters: Mapping[str, object] = field(default_factory=dict)
```

`scenario` is a provider-owned stable identifier. `parameters` carries controlled input
without teaching Proteus the provider's native schema.

### `SafetyEvidence`

One normalized evidence return:

```python
@dataclass(frozen=True)
class SafetyEvidence:
    mode: AuditMode
    evaluable: bool
    exposure: Exposure = Exposure.UNKNOWN
    observed_behavior: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    observation: AuditObservation = field(default_factory=AuditObservation)
    reason: str = ""
```

The provider does not return a verdict. It returns observed evidence and whether the
requested invariant can be evaluated. The independent evaluator owns the verdict.

### `SafetyEvidenceProvider`

```python
class SafetyEvidenceProvider(Protocol):
    name: str

    def collect(
        self,
        request: SafetyEvidenceRequest,
        context: AuditContext,
    ) -> SafetyEvidence: ...
```

The provider may read the case-specific evidence directory and disposable snapshot in the
context. It never receives the source run root.

### `SafetyEvidenceAdapter`

```python
@runtime_checkable
class SafetyEvidenceAdapter(Protocol):
    def safety_evidence_provider(self) -> SafetyEvidenceProvider: ...
```

This is an optional companion protocol. `HarnessAdapter` is unchanged. An evaluator may
also receive an explicit provider, which takes precedence over adapter discovery.

## Evaluator

`proteus/safety/evaluator.py` adds:

### `SafetyMeasurementDefinition`

An immutable case definition containing:

- `case_id`;
- `taxonomy`;
- `expected_behavior`;
- one concrete failure statement for a violated invariant; and
- a `SafetyEvidenceRequest`.

### `SafetyMeasurementCase`

An `AuditCase` implementation bound to one definition and provider. Its mapping is exact:

1. No provider -> `not_evaluated`.
2. Provider exception -> allowed to propagate; the existing runner records `error` and
   continues.
3. Evidence mode differs from the request -> `invalid`.
4. `evaluable=False` with a non-null invariant, or without a reason -> `invalid`.
5. `evaluable=False` with a reason -> `not_evaluated`, retaining exposure and any
   diagnostic evidence.
6. `evaluable=True` without evidence references or without a determinate
   `safety_invariant_violated` value -> `invalid`.
7. `safety_invariant_violated=True` -> `fail` with the definition's concrete failure.
8. `safety_invariant_violated=False` -> `pass`.

`not_exposed` is represented as `status=not_evaluated` and
`exposure=not_exposed`; it never becomes a pass.

For `contained_replay` and `matched_replay`, the case copies `context.snapshot_root` into
a fresh temporary directory and passes a replaced context to the provider. The copy is
discarded after collection. Artifact providers receive the runner's materialized snapshot
directly under the existing trusted-read contract.

### `SafetyMeasurementEvaluator`

An `AuditSuite` implementation containing a name, version, definitions, and optional
explicit provider. `cases(adapter, surfaces)` resolves the provider in this order:

1. explicitly injected provider;
2. `adapter.safety_evidence_provider()` when the adapter implements
   `SafetyEvidenceAdapter`; or
3. no provider, which produces explicit `not_evaluated` results.

It returns one `SafetyMeasurementCase` per definition and therefore runs through the
existing `run_audit` publication path.

## Aki Validation Boundary

An Aki integration may translate native behavior evidence into `SafetyEvidence`:

- native hook events or model-boundary results become provider observations;
- paired state effects are independently inspected by the provider;
- absent permission evidence leaves `decision_allowed=None`;
- an X1 composition-selection invariant may still be determinate when ordered semantic
  results and paired state effects are complete; and
- deterministic scripted reachability keeps `causal_status=not_evaluated`.

That translation is a provider concern. The Proteus evaluator sees only the generic
request, evidence references, exposure, and nullable observation vector.

## Artifact and Report Compatibility

No sidecar schema or layout change is required. `AuditResult` already carries taxonomy,
status, exposure, expected and observed behavior, evidence references, observation, and
agent self-assessment. The report already displays audit counts separately.

## Testing

All core tests are offline and use provider fixtures with arbitrary scenario and surface
names.

1. Exposed, evidenced, non-violating evidence passes.
2. A determinate violation fails with a concrete failure.
3. Missing provider and unsupported evidence are `not_evaluated`.
4. `not_exposed` remains separate and is not a pass.
5. Mode mismatch, missing reason, contradictory evaluability, missing invariant, and
   missing evidence references are `invalid`.
6. Provider exceptions become per-case `error` results and later cases continue.
7. Replay providers receive a distinct disposable snapshot and cannot mutate the artifact
   snapshot or source sweep.
8. Explicit provider resolution takes precedence over optional adapter discovery.
9. Existing audit, CLI, report, goal, and smoke tests remain green.

## Definition of Done

- `SafetyMeasurementEvaluator` is public from `proteus.safety`.
- It runs through `run_audit` and produces ordinary audit sidecars.
- The evaluator contains no Aki imports or Aki-native vocabulary.
- Replay evidence is collected from a disposable case-private copy.
- Missing, invalid, failing, passing, and provider-error evidence retain distinct outcomes.
- Evolution, selection, and promotion code remain unchanged.
