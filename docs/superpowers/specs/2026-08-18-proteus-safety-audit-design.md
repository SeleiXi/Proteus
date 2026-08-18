# Proteus Safety Audit Design

Date: 2026-08-18  
Status: design approved in conversation; written specification awaiting review

## Summary

Proteus will gain a post-run safety-audit subsystem that reads completed evolution
trajectories and writes independent audit artifacts. The subsystem is observational: it
does not participate in prompts, goals, evaluator feedback, selection, snapshotting,
promotion, rollback, or later episodes.

The existing Proteus evolution path remains unchanged. Safety auditing is a second path
over immutable trajectory evidence:

```text
Evolution path
GoalConfig -> episode -> existing selection -> snapshot

Audit path
snapshot + trace + Surface manifest
        |-- agent self-assessment (diagnostic evidence only)
        `-- independent audit oracle
                    |
              audit sidecars
```

The earlier Aki design is reused at the right level. Its multidimensional measurement
model becomes the portable taxonomy. Its Memory, Skills, Authored Tools, Composition, and
Shared case allocation remains an Aki-specific audit pack rather than becoming a hard-coded
Proteus hierarchy.

## Problem

Proteus currently measures structural change, behavioural distance, goal performance, and
evaluator feedback. Its `EvalResult` is a scalar task result, and its evaluator wiring may
affect the agent or outer-loop selection. Safety evidence has different semantics:

- a safety failure must not be averaged with task utility;
- unavailable evidence must remain `not_evaluated` rather than becoming a pass;
- agent self-evaluation is a claim by the subject, not ground truth;
- an audit must be able to cite trace, snapshot, and external evidence; and
- for this research use, observing safety must not change the evolution being observed.

Encoding safety cases as hidden `Goal` evaluators would conflate these meanings and could
silently make the audit part of selection. The audit therefore needs a separate result
model and a separate execution entrypoint.

## Goals

1. Audit completed Proteus runs without changing their trajectory or execution semantics.
2. Reuse `HarnessAdapter`, `Surface`, `ActionEvent`, and git snapshots as the portable
   evidence boundary.
3. Represent each case with an orthogonal taxonomy instead of five mixed peer categories.
4. Preserve independent-oracle verdicts, agent self-assessments, evidence references, and
   explicit missingness.
5. Support adapter-independent audit infrastructure and adapter-specific audit packs.
6. Store audit artifacts outside the evolving harness and show them separately from task
   scores in reports.
7. Provide a small built-in instrument-integrity suite that validates the audit pipeline
   without claiming general harness safety.

## Non-goals

- No safety gate, promotion policy, rejection, rollback, or mutation approval.
- No audit feedback to the agent and no safety result in `GoalConfig` or `EvalResult`.
- No changes to `RunConfig`, `SweepConfig`, or the episode loop for the first version.
- No scalar safety score or ranking across unlike cases.
- No claim that static posture, trace presence, or an agent statement proves downstream
  safety.
- No automatic port of all 30 Aki cases into generic Proteus cases. Aki-native semantics
  remain in an Aki audit pack.
- No execution of historical or agent-authored code on the host by the generic artifact
  runner.

## Taxonomy

Every audit case records seven fields:

```text
target
x initiating_source
x episode_phase
x evolution_stage
x failure_mode
x evidence
x outcome
```

### Target

`target` names the object or authority boundary being evaluated.

- Prefer an adapter-declared `Surface` name when the case concerns persistent mutable
  harness state.
- Use a framework boundary name when the target is not a `Surface`: `trace`, `evaluator`,
  `sandbox`, `audit`, or `external_effect`.
- Record `scope` with the target: `local`, `composed`, `shared`, or `external`.

Memory, skills, and authored tools are therefore surface targets. Composition is a scope or
path across targets. Shared/infrastructure cases are prerequisites or shared-scope cases,
not peer surfaces.

### Initiating source

`initiating_source` describes what introduced the condition:

- benign model or stochastic error;
- task, specification, or operator ambiguity;
- untrusted external input or adversarial input;
- compromised tool, dependency, or peer;
- endogenous optimization, drift, or scheming; or
- evaluator or oracle fault.

Case packs may use more precise strings while retaining one of these parent meanings.

### Episode phase

`episode_phase` uses Proteus's existing phases: `observe`, `propose`, `act`, and `reflect`.
A case may span multiple phases and records both introduction and observation phases when
they differ.

### Evolution stage

`evolution_stage` is distinct from an episode phase:

- `baseline`;
- `candidate_creation`;
- `evaluation`;
- `selection`;
- `committed_state`;
- `persistence`; or
- `repair_or_rollback`.

An audit does not perform selection or rollback; it may observe evidence produced at those
stages.

### Failure mode

The portable parent failure modes are:

- goal, specification, or planning error;
- retrieval or persistent-state corruption;
- unsafe action, tool misuse, or authority breach;
- coordination or cross-surface composition failure;
- evaluator, reward, or evidence gaming;
- deception, monitoring evasion, or sabotage;
- unsafe self-modification; and
- persistence, cascade, termination, or recovery failure.

### Evidence

Evidence has two independent attributes:

- `authority`: `agent_claim`, `harness_trace`, `independent_oracle`, or
  `external_observation`;
- `method`: `static`, `artifact`, `executable_replay`, `matched_intervention`, or
  `deployment_observation`.

Agent self-evaluation is preserved as diagnostic evidence. It can be compared with the
oracle but can never determine the oracle result.

### Outcome

The primary status is one of:

- `pass`;
- `fail`;
- `not_evaluated`;
- `invalid`; or
- `error`.

Exposure is recorded separately as `exposed`, `not_exposed`, or `unknown`; lack of exposure
does not silently become a safety pass.

The observation vector has nullable fields so missing evidence remains explicit:

- `attempted`;
- `decision_allowed`;
- `state_changed`;
- `safety_invariant_violated`;
- `downstream_harm_observed`;
- `persisted_or_propagated`;
- `detected_or_contained`;
- `repaired_or_rolled_back`;
- `utility_preserved`; and
- `causal_status` (`established`, `correlated`, or `not_evaluated`).

## Architecture

### Package boundary

The audit subsystem lives under `proteus/safety/` and depends on Proteus core abstractions.
Proteus core does not import the safety package.

Proposed modules:

```text
proteus/safety/
  __init__.py       public audit types
  model.py          taxonomy, context, result, and protocol types
  runner.py         completed-sweep traversal and artifact audit execution
  loading.py        module:object suite loading
  integrity.py      small built-in instrument-integrity suite
```

The CLI adds a top-level `proteus audit` command. The existing `run`, `measure`, `report`,
and `watch` behaviour remains unchanged.

### Public types

`AuditTaxonomy` is immutable and contains the seven taxonomy fields. Fields that need
harness-specific detail are strings or tuples of strings rather than a closed global enum.

`AuditObservation` is immutable and contains the nullable outcome vector.

`AuditAssessment` is the case-local return value. It contains status, exposure, observed
behaviour, concrete failures, evidence references, the independent observation vector, and
an error string when applicable. It does not duplicate suite or run identity.

`AuditResult` contains:

- suite name and version;
- case ID;
- run ID, adapter, arm, seed, and episode;
- taxonomy;
- status and exposure;
- expected and observed behaviour;
- concrete failures;
- evidence references relative to the audit directory;
- the independent observation vector;
- all reflect-phase texts as untrusted agent self-assessment signals; and
- a short error string only when status is `error`.

`AuditContext` contains:

- immutable run metadata;
- the materialized snapshot path;
- the original run root for read-only adapter trace parsing;
- adapter name and declared surfaces;
- normalized action events for the episode;
- all reflect-phase texts collected as untrusted agent self-assessment signals; and
- a case-specific evidence directory.

`AuditCase` is a protocol with `case_id`, `taxonomy`, and
`evaluate(context) -> AuditAssessment`. The runner combines the assessment with suite,
run, episode, taxonomy, and agent-signal metadata to create `AuditResult`.

`AuditSuite` is a protocol with `name`, `version`, and
`cases(adapter, surfaces) -> Sequence[AuditCase]`.

### Audit runner

The runner receives:

- a completed sweep root;
- an adapter instance;
- an audit suite; and
- an explicit audit ID.

It performs these steps:

1. Read `manifest.json` and `seeds.jsonl`.
2. Resolve each completed run by its existing opaque run ID.
3. For every completed episode, resolve the episode commit through
   `snapshot.commit_for_episode`.
4. Materialize that commit into a temporary directory.
5. Read the original episode trace through `adapter.read_trace`.
6. Extract reflect-phase text as the untrusted agent self-assessment signal.
7. Run each applicable artifact case against the materialized copy.
8. Append one result per case to the audit JSONL.
9. Write a summary and a discoverable audit index after all results are durable.

The generic runner never calls `adapter.run_episode`, executes files from the snapshot, or
writes into the run root. A replay-oriented adapter pack may launch a contained subprocess
or container against another disposable materialization, but that execution is outside the
generic artifact runner and must never mount the original trajectory read-write.

### Audit modes

The result schema records one of three evidence modes:

1. `artifact`: read immutable snapshot and trace evidence only;
2. `contained_replay`: run a probe on a disposable snapshot copy under an OS boundary;
3. `matched_replay`: run benign and hazardous/corrected twins from the same immutable
   checkpoint under an OS boundary.

The first implementation fully implements artifact mode. The protocols and result schema
permit contained and matched replay packs, but the generic runner does not provide an
unsafe host-execution shortcut.

### Built-in instrument-integrity suite

The built-in suite proves that the audit substrate can observe its inputs. It does not
claim broad safety. Its cases check:

- the episode snapshot exists and can be materialized;
- the adapter returns a normalized trace for the recorded episode;
- every returned event uses a canonical Proteus phase; and
- agent self-assessment availability is reported, not required.

Missing evidence produces `not_evaluated` or `invalid` according to whether the evidence is
unavailable or malformed. A valid but empty trace is not a safety pass.

### Aki audit pack

The existing Aki panel remains adapter-specific because its cases depend on native memory
retrieval, skill search and precedence, authored-tool loading, permission decisions, tool
results, and external effects that the generic `ActionEvent` does not fully expose.

The integration boundary is a separate Aki suite or bridge that:

- locates a Proteus Aki run by run ID and episode;
- materializes the episode snapshot;
- invokes Aki's existing contained behaviour runner on a disposable copy;
- maps Aki case metadata into the portable taxonomy;
- preserves Aki `not_exposed` as the separate exposure field;
- maps missing native evidence to `not_evaluated`; and
- writes ordinary Proteus audit sidecars.

This bridge is designed after the generic audit substrate is stable. The first change does
not copy Aki-native runners into Proteus or make Proteus depend on the Aki repository.

## Artifact layout

Audits live at the sweep level, outside every evolving run:

```text
<sweep>/audits/
  index.json
  <audit-id>/
    manifest.json
    results.jsonl
    summary.json
    evidence/
      <run-id>/
        episode-<n>/
          <case-id>/
```

`manifest.json` records the suite identity and version, adapter name, source sweep path,
run IDs, configured taxonomy version, and creation time.

`results.jsonl` is append-only during one audit execution. Each line is self-contained.

`summary.json` contains counts by status, exposure, target, failure mode, and evidence
method. It contains no composite safety score.

`index.json` lists completed audits for report discovery. It is published atomically after
the audit manifest, results, and summary exist.

An audit command refuses to overwrite an existing `<audit-id>` directory. The operator must
choose a new ID, preserving previous and failed-run evidence.

## Report integration

The existing evolution report gains a separate `Safety audits` section. It reads
`audits/index.json` when present and otherwise renders the existing page unchanged.

The section shows:

- audit ID, suite, version, and completion state;
- counts for pass, fail, not evaluated, invalid, and error;
- counts by target and evidence method; and
- relative links to the summary and result artifacts.

It does not merge audit status with evaluator scores, run completion, or accepted/rejected
episode status.

## CLI

The command shape is:

```bash
proteus audit \
  --harness minimal \
  --out runs/demo \
  --suite proteus.safety.integrity:SUITE \
  --audit-id instrument-integrity-v1
```

`--harness` uses the existing adapter loader. `--suite` accepts `module:object`, matching
Proteus's harness extension style. The object may be an `AuditSuite` instance, an
`AuditSuite` class, or a zero-argument factory.

The default suite is the built-in instrument-integrity suite. The default audit ID is its
suite name, and an existing destination is an error rather than an overwrite.

## Failure semantics

- Missing sweep metadata is a command error before any audit directory is published.
- A missing episode snapshot creates an `invalid` result for each applicable case and the
  runner continues.
- An unavailable adapter-native signal creates `not_evaluated`; it is not imputed.
- Malformed trace data creates `invalid`.
- A case exception creates an `error` result with a short error description; remaining
  cases and runs continue.
- An interrupted audit keeps its partial directory but is absent from `audits/index.json`.
- No audit failure changes `seeds.jsonl`, `eval_history.json`, run completion, snapshots, or
  the process exit status of the completed evolution run.
- The audit command exits non-zero only for preflight, persistence, or publication failure.
  Individual `fail`, `not_evaluated`, `invalid`, or case-level `error` results remain audit
  evidence and do not change the command exit code.

## Compatibility

- Existing library and CLI users see no behaviour change unless they run `proteus audit`.
- Existing adapters need no new methods for artifact audits.
- Existing goal evaluators and selection remain untouched.
- Suites that require richer native evidence own an adapter-specific bridge and return
  `not_evaluated` when the evidence is unavailable.
- The audit package has no new third-party dependency.

## Testing

All tests are offline and use the minimal adapter or synthetic suites.

1. Model validation:
   - pass assessments cannot contain failures;
   - fail assessments require concrete failures;
   - error assessments require an error string;
   - evidence paths must be relative to the audit directory.
2. Runner isolation:
   - audit a completed minimal sweep;
   - assert original harness files, snapshot HEADs, eval histories, and seed records are
     unchanged;
   - assert audit artifacts exist only under `<sweep>/audits/`.
3. Missingness and resilience:
   - missing snapshot becomes `invalid`;
   - unavailable signal becomes `not_evaluated`;
   - one throwing case becomes `error` without stopping later cases.
4. Self-assessment separation:
   - reflect text is captured as an agent claim;
   - changing that claim cannot change an independent case verdict.
5. Loading and CLI:
   - load instance, class, and factory suites;
   - reject malformed suites and existing audit IDs;
   - `proteus audit --help` documents the boundary.
6. Reporting:
   - the report remains unchanged when no audit index exists;
   - audit counts appear in a separate section when an index exists;
   - no composite score is rendered.
7. Regression:
   - existing tests continue to pass unchanged.

## Documentation

README and recipes will document:

- the difference between task evaluation, agent self-evaluation, and independent safety
  audit;
- the audit-only, zero-feedback boundary;
- the artifact layout and CLI command;
- why `not_evaluated` differs from a pass; and
- how an adapter-specific suite can map native evidence into the portable taxonomy.

## Implementation order

1. Add the typed audit model and validation tests.
2. Add suite loading and the immutable artifact runner with isolation tests.
3. Add the built-in instrument-integrity suite.
4. Add the `proteus audit` CLI.
5. Add audit discovery and the separate report section.
6. Add reader-oriented documentation and run the offline verification sweep.
7. Design the Aki bridge against the stable API as a separate follow-up change; do not
   couple the first audit substrate to the private Aki checkout.

## Definition of done

- A completed offline sweep can be audited without changing any trajectory artifact.
- Results carry the portable taxonomy, explicit missingness, independent evidence, and the
  agent self-assessment signal.
- Existing evolution behaviour and tests remain unchanged.
- The report shows audit results separately and never emits a scalar safety score.
- The generic runner does not execute snapshot code on the host.
- The public documentation states the exact claim boundary.
