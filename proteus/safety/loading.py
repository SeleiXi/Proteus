"""Load audit suites through Proteus's module:object extension style."""

from __future__ import annotations

import importlib
from typing import cast

from proteus.safety.model import AuditSuite


def _looks_like_suite(value: object) -> bool:
    return (
        isinstance(getattr(value, "name", None), str)
        and isinstance(getattr(value, "version", None), str)
        and callable(getattr(value, "cases", None))
    )


def load_suite(spec: str) -> AuditSuite:
    """Resolve an audit-suite instance, class, or zero-argument factory."""
    module_name, separator, object_name = spec.partition(":")
    if not separator or not module_name or not object_name:
        raise ValueError("suite must use <module>:<object>")
    module = importlib.import_module(module_name)
    value = getattr(module, object_name)
    if isinstance(value, type) or (callable(value) and not _looks_like_suite(value)):
        value = value()
    for name, predicate in (
        ("name", lambda item: isinstance(item, str) and bool(item.strip())),
        ("version", lambda item: isinstance(item, str) and bool(item.strip())),
        ("cases", callable),
    ):
        if not predicate(getattr(value, name, None)):
            raise TypeError(f"audit suite needs valid {name}")
    return cast(AuditSuite, value)
