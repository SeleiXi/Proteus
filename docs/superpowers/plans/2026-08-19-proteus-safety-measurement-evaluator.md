# Proteus Safety Measurement Evaluator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one generic, audit-only Proteus safety measurement evaluator that converts provider evidence into independent safety verdicts and ordinary audit sidecars.

**Architecture:** Extend the existing `proteus.safety` model with provider-neutral evidence contracts, then implement an `AuditSuite`/`AuditCase` adapter that reuses `run_audit`. Artifact evidence uses the existing disposable materialization; replay evidence gets an additional case-private copy. Aki remains an external validation provider rather than shaping the evaluator.

**Tech Stack:** Python 3.10+, dataclasses, protocols, pathlib, pytest, Ruff; no new dependency.

**Spec:** `docs/superpowers/specs/2026-08-19-proteus-safety-measurement-evaluator-design.md`

## Global Constraints

- The evaluator is post-run and must not affect prompts, goals, selection, promotion, rollback, or later episodes.
- Preserve `pass`, `fail`, `not_evaluated`, `invalid`, and `error` separately.
- Preserve `Exposure` independently from verdict.
- Do not add Aki-native names or schemas to `proteus/safety`.
- Do not change `HarnessAdapter`, `GoalConfig`, `EvalResult`, `RunConfig`, or `SweepConfig`.
- Replay providers own OS containment and receive only a disposable snapshot copy.
- Add no third-party dependency.

---

### Task 1: Generic evidence contracts

**Files:**
- Modify: `proteus/safety/model.py`
- Modify: `proteus/safety/__init__.py`
- Test: `tests/test_safety_evaluator.py`

**Interfaces:**
- Produces: `SafetyEvidenceRequest`, `SafetyEvidence`, `SafetyEvidenceProvider`, and `SafetyEvidenceAdapter`.

- [ ] **Step 1: Write the failing contract tests**

Create `tests/test_safety_evaluator.py` with a request using
`AuditMode.CONTAINED_REPLAY`, a determinate `SafetyEvidence`, and a runtime-checkable
fixture adapter implementing `safety_evidence_provider()`. Assert the request preserves its
opaque parameters, evidence preserves exposure independently, and provider discovery is
structural.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `PYTHONPATH=. pytest tests/test_safety_evaluator.py -q`
Expected: collection fails because the new public contracts do not exist.

- [ ] **Step 3: Implement the minimal public contracts**

Add the four exact contracts from the spec to `proteus/safety/model.py`, using
`collections.abc.Mapping`, `typing.Protocol`, and `typing.runtime_checkable`. Export them
from `proteus/safety/__init__.py`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `PYTHONPATH=. pytest tests/test_safety_evaluator.py -q`
Expected: contract tests pass.

### Task 2: Verdict mapping and provider resolution

**Files:**
- Create: `proteus/safety/evaluator.py`
- Modify: `proteus/safety/__init__.py`
- Modify: `tests/test_safety_evaluator.py`

**Interfaces:**
- Consumes: `SafetyEvidenceRequest`, `SafetyEvidence`, `SafetyEvidenceProvider`, `SafetyEvidenceAdapter`, `AuditAssessment`, and `AuditContext`.
- Produces: `SafetyMeasurementDefinition`, `SafetyMeasurementCase`, and `SafetyMeasurementEvaluator`.

- [ ] **Step 1: Write failing verdict tests**

Add literal provider fixtures and assert:

- safe determinate evidence -> `pass`;
- violated invariant -> `fail` with the definition failure;
- no provider -> `not_evaluated`;
- `evaluable=False` with a reason -> `not_evaluated`;
- `Exposure.NOT_EXPOSED` remains `not_exposed` while status is `not_evaluated`;
- mode mismatch -> `invalid`;
- unevaluable evidence without a reason -> `invalid`;
- unevaluable evidence with a determinate invariant -> `invalid`;
- evaluable evidence without an invariant -> `invalid`; and
- evaluable evidence without evidence references -> `invalid`.

Each test calls a real `SafetyMeasurementCase.evaluate(context)` and uses literal expected
statuses rather than reusing evaluator helpers.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `PYTHONPATH=. pytest tests/test_safety_evaluator.py -q`
Expected: import failure for `proteus.safety.evaluator`.

- [ ] **Step 3: Implement the minimal evaluator**

Implement the exact precedence in the spec. For replay modes, use
`tempfile.TemporaryDirectory`, `shutil.copytree`, and `dataclasses.replace` to pass a
case-private `snapshot_root` to the provider. Implement explicit-provider precedence and
optional adapter-provider discovery in `SafetyMeasurementEvaluator.cases()`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `PYTHONPATH=. pytest tests/test_safety_evaluator.py -q`
Expected: all evaluator tests pass.

### Task 3: Existing runner integration and isolation

**Files:**
- Modify: `tests/test_safety_evaluator.py`
- Modify: `docs/RECIPES.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `run_audit`, `SafetyMeasurementEvaluator`, and the existing sidecar layout.
- Produces: verified ordinary `manifest.json`, `results.jsonl`, `summary.json`, and audit index entries.

- [ ] **Step 1: Write the failing integration test**

Create a completed minimal sweep, run a two-definition `SafetyMeasurementEvaluator`
through `run_audit`, and assert literal result statuses `pass` then `fail`. The replay
provider writes one evidence JSON per case and mutates its received snapshot; assert the
second case and the source sweep still see the original state. Add a throwing provider
case and assert the existing runner records `error` and continues.

- [ ] **Step 2: Run the integration test and verify RED**

Run: `PYTHONPATH=. pytest tests/test_safety_evaluator.py -q`
Expected: failure if replay copies, result mapping, or provider resolution are incomplete.

- [ ] **Step 3: Complete the minimal integration behavior**

Make only evaluator-local changes required by the failing test. Do not modify evolution,
selection, or the sidecar publisher.

- [ ] **Step 4: Document the evaluator/provider boundary**

Update README and recipes with one generic example. State that providers return evidence,
the evaluator owns verdicts, replay providers own OS containment, and an Aki provider is
only one possible integration.

- [ ] **Step 5: Run focused verification**

Run: `PYTHONPATH=. pytest tests/test_safety_evaluator.py tests/test_safety_runner.py tests/test_safety_model.py -q`
Expected: all pass.

### Task 4: Regression and branch review

**Files:**
- Verify all changed files.

**Interfaces:**
- Produces: a reviewed branch with no changes to evolution or promotion paths.

- [ ] **Step 1: Run the complete offline suite**

Run: `PYTHONPATH=. pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Run lint and whitespace checks**

Run: `ruff check proteus/safety tests/test_safety_evaluator.py`
Expected: clean.

Run: `git diff --check`
Expected: clean.

- [ ] **Step 3: Inspect the final scope**

Run: `git diff --stat codex/safety-audit...HEAD` and
`git diff codex/safety-audit...HEAD -- proteus/core proteus/sweep.py`
Expected: the feature delta is limited to `proteus/safety`, tests, and documentation; no
evolution or promotion change appears.

- [ ] **Step 4: Commit the evaluator**

```bash
git add proteus/safety tests/test_safety_evaluator.py README.md docs/RECIPES.md \
  docs/superpowers/specs/2026-08-19-proteus-safety-measurement-evaluator-design.md \
  docs/superpowers/plans/2026-08-19-proteus-safety-measurement-evaluator.md
git commit -m "feat(safety): add provider-backed measurement evaluator"
```
