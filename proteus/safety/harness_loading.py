"""Load module-first harness-safety suites through the extension convention."""

from __future__ import annotations

import importlib
from typing import cast

from proteus.safety.plugins import HarnessSafetyCaseSuite


def _looks_like_suite(value: object) -> bool:
    return (
        isinstance(getattr(value, "name", None), str)
        and isinstance(getattr(value, "version", None), str)
        and callable(getattr(value, "definitions", None))
        and callable(getattr(value, "provider", None))
    )


def load_harness_safety_suite(spec: str) -> HarnessSafetyCaseSuite:
    """Resolve a suite instance, class, or zero-argument factory."""
    module_name, separator, object_name = spec.partition(":")
    if not separator or not module_name or not object_name:
        raise ValueError("harness safety suite must use <module>:<object>")
    module = importlib.import_module(module_name)
    value = getattr(module, object_name)
    if isinstance(value, type) or (callable(value) and not _looks_like_suite(value)):
        value = value()
    for name, predicate in (
        ("name", lambda item: isinstance(item, str) and bool(item.strip())),
        ("version", lambda item: isinstance(item, str) and bool(item.strip())),
        ("definitions", callable),
        ("provider", callable),
    ):
        if not predicate(getattr(value, name, None)):
            raise TypeError(f"harness safety suite needs valid {name}")
    return cast(HarnessSafetyCaseSuite, value)
