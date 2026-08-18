from __future__ import annotations

import sys
import types

import pytest

from proteus.safety.loading import load_suite


class FixtureSuite:
    name = "fixture"
    version = "1"

    def cases(self, adapter, surfaces):
        return ()


def test_loads_instance_class_and_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("fixture_audit_suites")
    module.INSTANCE = FixtureSuite()
    module.SUITE_CLASS = FixtureSuite
    module.factory = lambda: FixtureSuite()
    monkeypatch.setitem(sys.modules, module.__name__, module)

    assert load_suite("fixture_audit_suites:INSTANCE").name == "fixture"
    assert load_suite("fixture_audit_suites:SUITE_CLASS").name == "fixture"
    assert load_suite("fixture_audit_suites:factory").name == "fixture"


@pytest.mark.parametrize("spec", ["", "fixture", ":suite", "fixture:"])
def test_suite_spec_requires_module_and_object(spec: str) -> None:
    with pytest.raises(ValueError, match="<module>:<object>"):
        load_suite(spec)


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("name", ""),
        ("version", ""),
        ("cases", None),
    ],
)
def test_loaded_suite_requires_public_contract(
    monkeypatch: pytest.MonkeyPatch, attribute: str, value: object
) -> None:
    module = types.ModuleType(f"bad_audit_suite_{attribute}")
    suite = FixtureSuite()
    setattr(suite, attribute, value)
    module.SUITE = suite
    monkeypatch.setitem(sys.modules, module.__name__, module)

    with pytest.raises(TypeError, match=attribute):
        load_suite(f"{module.__name__}:SUITE")
