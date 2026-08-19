"""Offline test runner for environments without pytest. Exit code = failures."""
import pathlib
import sys
import tempfile
import traceback


def main() -> int:
    import tests.test_aki_adapter as A
    import tests.test_bench as B
    import tests.test_goals as G
    import tests.test_instrument as I
    import tests.test_smoke as S
    tmp = pathlib.Path(tempfile.mkdtemp())
    passed = failed = 0
    for mod in (G, S, A, B, I):
        for name in [n for n in dir(mod) if n.startswith("test_")]:
            fn = getattr(mod, name)
            d = tmp / mod.__name__ / name
            d.mkdir(parents=True)
            try:
                fn(d) if fn.__code__.co_argcount else fn()
                passed += 1
            except Exception:  # noqa: BLE001 - a runner reports failures, it does not raise
                print(f"FAIL {name}")
                traceback.print_exc(limit=3)
                failed += 1
    print(f"{passed} passed, {failed} failed")
    return failed

if __name__ == "__main__":
    sys.exit(main())
