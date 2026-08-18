"""Offline test runner for environments without pytest. Exit code = failures."""
import sys, tempfile, pathlib, traceback

def main() -> int:
    import tests.test_goals as G, tests.test_smoke as S, tests.test_aki_adapter as A
    import tests.test_bench as B
    tmp = pathlib.Path(tempfile.mkdtemp())
    passed = failed = 0
    for mod in (G, S, A, B):
        for name in [n for n in dir(mod) if n.startswith("test_")]:
            fn = getattr(mod, name)
            d = tmp / mod.__name__ / name
            d.mkdir(parents=True)
            try:
                fn(d) if fn.__code__.co_argcount else fn()
                passed += 1
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc(limit=3)
                failed += 1
    print(f"{passed} passed, {failed} failed")
    return failed

if __name__ == "__main__":
    sys.exit(main())
