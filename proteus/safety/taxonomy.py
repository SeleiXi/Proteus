"""Module-first taxonomy contracts for harness-safety case families."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from proteus.core.adapter import Surface

MODULE_SAFETY_TAXONOMY_VERSION = "proteus-module-safety/1"


class HarnessModule(str, Enum):
    AGENT_LOOP = "agent_loop"
    MEMORY = "memory"
    SKILLS = "skills"
    TOOLS = "tools"


class CaseScope(str, Enum):
    SINGLE_MODULE = "single_module"
    CROSS_MODULE = "cross_module"


class SafetyKind(str, Enum):
    NON_ADVERSARIAL = "non_adversarial"
    ADVERSARIAL = "adversarial"


class EvaluationArm(str, Enum):
    MODEL_REFERENCE = "model_reference"
    FULL_HARNESS = "full_harness"
    MODULE_INTERVENTION = "module_intervention"


class SafetyExposure(str, Enum):
    EXPOSED = "exposed"
    NOT_EXPOSED = "not_exposed"
    UNKNOWN = "unknown"


class SafetyStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"
    INVALID = "invalid"
    ERROR = "error"


class HarnessContribution(str, Enum):
    PROTECTIVE_STEERING = "protective_steering"
    PROTECTIVE_CONTAINMENT = "protective_containment"
    HARMFUL_INDUCTION = "harmful_induction"
    HARMFUL_AMPLIFICATION = "harmful_amplification"
    NEUTRAL = "neutral"
    NOT_EVALUATED = "not_evaluated"


class ModuleCausalStatus(str, Enum):
    ESTABLISHED = "established"
    NOT_EVALUATED = "not_evaluated"


class TransitionDirection(str, Enum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    UNCHANGED = "unchanged"
    MIXED = "mixed"
    NOT_EVALUATED = "not_evaluated"


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")


def _require_text_items(label: str, values: tuple[str, ...]) -> None:
    if not values or not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError(f"{label} must contain non-empty values")


@dataclass(frozen=True)
class FaultModel:
    source: str
    condition: str

    def __post_init__(self) -> None:
        _require_text("fault source", self.source)
        _require_text("fault condition", self.condition)


@dataclass(frozen=True)
class ThreatModel:
    attacker: str
    defender: str
    entry_point: str
    attacker_capabilities: tuple[str, ...]
    attacker_capability_limits: tuple[str, ...]
    attacker_objective: str
    protected_invariant: str
    defensive_control_under_test: str

    def __post_init__(self) -> None:
        _require_text("attacker", self.attacker)
        _require_text("defender", self.defender)
        _require_text("entry point", self.entry_point)
        _require_text_items("attacker capabilities", self.attacker_capabilities)
        _require_text_items("attacker capability limits", self.attacker_capability_limits)
        _require_text("attacker objective", self.attacker_objective)
        _require_text("protected invariant", self.protected_invariant)
        _require_text("defensive control under test", self.defensive_control_under_test)


@dataclass(frozen=True)
class PermissionBoundary:
    actor: str
    requested_operation: str
    allowed_capabilities: tuple[str, ...]
    prohibited_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text("permission actor", self.actor)
        _require_text("requested operation", self.requested_operation)
        _require_text_items("allowed capabilities", self.allowed_capabilities)
        _require_text_items("prohibited capabilities", self.prohibited_capabilities)


@dataclass(frozen=True)
class ModuleBinding:
    module: HarnessModule
    surface_names: tuple[str, ...] = ()
    runtime_evidence: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.module, HarnessModule):
            raise TypeError("module binding requires a HarnessModule")
        if len(self.surface_names) != len(set(self.surface_names)):
            raise ValueError("module binding surface names must be unique")
        if any(not isinstance(name, str) or not name.strip() for name in self.surface_names):
            raise ValueError("module binding surface names must be non-empty")
        if not self.surface_names and not self.runtime_evidence:
            raise ValueError("module binding requires a surface or runtime evidence")


@dataclass(frozen=True)
class HarnessSafetyProfile:
    bindings: tuple[ModuleBinding, ...]

    def __post_init__(self) -> None:
        modules = [binding.module for binding in self.bindings]
        if len(modules) != len(set(modules)):
            raise ValueError("harness safety profile has duplicate module bindings")

    def validate_surfaces(self, surfaces: Sequence[Surface]) -> None:
        declared = {surface.name for surface in surfaces}
        for binding in self.bindings:
            for name in binding.surface_names:
                if name not in declared:
                    raise ValueError(f"module binding references undeclared surface: {name}")

    def binding_for(self, module: HarnessModule) -> ModuleBinding | None:
        return next((binding for binding in self.bindings if binding.module is module), None)


@dataclass(frozen=True)
class SafetyCaseFamilyDefinition:
    family_id: str
    primary_module: HarnessModule
    supporting_modules: tuple[HarnessModule, ...]
    scope: CaseScope
    safety_kind: SafetyKind
    scenario: str
    safety_invariant: str
    behavior_failure: str
    module_failure: str
    evaluation_arms: tuple[EvaluationArm, ...]
    threat_model: ThreatModel | None = None
    fault_model: FaultModel | None = None
    permission_boundary: PermissionBoundary | None = None
    intervention_expected_violation: bool | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("family ID", self.family_id),
            ("scenario", self.scenario),
            ("safety invariant", self.safety_invariant),
            ("behavior failure", self.behavior_failure),
            ("module failure", self.module_failure),
        ):
            _require_text(label, value)
        self._validate_modules()
        self._validate_arms()
        self._validate_source()

    def _validate_modules(self) -> None:
        if not isinstance(self.primary_module, HarnessModule):
            raise TypeError("primary module requires a HarnessModule")
        if not isinstance(self.scope, CaseScope):
            raise TypeError("case family scope requires a CaseScope")
        if self.primary_module in self.supporting_modules:
            raise ValueError("primary module cannot also be a supporting module")
        if len(self.supporting_modules) != len(set(self.supporting_modules)):
            raise ValueError("supporting modules must be unique")
        if not all(isinstance(module, HarnessModule) for module in self.supporting_modules):
            raise TypeError("supporting modules require HarnessModule values")
        if self.scope is CaseScope.SINGLE_MODULE and self.supporting_modules:
            raise ValueError("single-module family cannot have supporting modules")
        if self.scope is CaseScope.CROSS_MODULE and not self.supporting_modules:
            raise ValueError("cross-module family requires supporting modules")

    def _validate_arms(self) -> None:
        if not all(isinstance(arm, EvaluationArm) for arm in self.evaluation_arms):
            raise TypeError("evaluation arms require EvaluationArm values")
        if len(self.evaluation_arms) != len(set(self.evaluation_arms)):
            raise ValueError("evaluation arms must be unique")
        for required in (EvaluationArm.MODEL_REFERENCE, EvaluationArm.FULL_HARNESS):
            if required not in self.evaluation_arms:
                raise ValueError(f"case family requires {required.value} evaluation arm")
        has_intervention = EvaluationArm.MODULE_INTERVENTION in self.evaluation_arms
        if has_intervention != (self.intervention_expected_violation is not None):
            raise ValueError(
                "module intervention and intervention_expected_violation are required together"
            )

    def _validate_source(self) -> None:
        if self.safety_kind is SafetyKind.ADVERSARIAL:
            if self.threat_model is None or self.fault_model is not None:
                raise ValueError("adversarial family requires only a threat model")
        elif self.safety_kind is SafetyKind.NON_ADVERSARIAL:
            if self.fault_model is None or self.threat_model is not None:
                raise ValueError("non-adversarial family requires only a fault model")
        else:
            raise TypeError("case family requires a SafetyKind")
