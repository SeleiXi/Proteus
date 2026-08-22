"""Mostly Basic Python Problems (MBPP) as lightweight external benchmark tasks.

Dataset: Google Research's ``sanitized-mbpp.json``, a hand-verified subset of MBPP
released under the Apache License 2.0 in google-research/google-research. The dataset is
not vendored; it is cached on first use, or supplied with ``PROTEUS_MBPP_PATH``.

Only the prompt and an empty ``solution.py`` are seeded. Reference code and assertions
remain in the dataset for grading and are never copied into the agent's task workspace.
"""

from __future__ import annotations

import ast
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
from urllib import request

from proteus.bench.task import BenchTask
from proteus.core.goal import EvalResult

DATA_URL = (
    "https://raw.githubusercontent.com/google-research/google-research/"
    "master/mbpp/sanitized-mbpp.json"
)
GRADE_TIMEOUT_S = 60
CALL_TIMEOUT_S = 15

_CODEC = '''\
import base64

_type = type
_isinstance = isinstance
_bool = bool
_int = int
_float = float
_complex = complex
_str = str
_bytes = bytes
_list = list
_tuple = tuple
_set = set
_frozenset = frozenset
_dict = dict

class _OpaqueValue:
    def __init__(self, truthy):
        self.truthy = truthy

    def __bool__(self):
        return self.truthy

def _encode(value):
    kind = _type(value)
    if value is None:
        return {"type": "none"}
    if kind is _bool:
        return {"type": "bool", "value": value}
    if kind is _int:
        return {"type": "int", "value": _str(value)}
    if kind is _float:
        return {"type": "float", "value": _str(value)}
    if kind is _complex:
        return {"type": "complex", "real": _str(value.real), "imag": _str(value.imag)}
    if kind is _str:
        return {"type": "str", "value": value}
    if kind is _bytes:
        return {"type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if _isinstance(value, _list):
        return {"type": "list", "items": [_encode(item) for item in value]}
    if _isinstance(value, _tuple):
        return {"type": "tuple", "items": [_encode(item) for item in value]}
    if _isinstance(value, (_set, _frozenset)):
        return {"type": "set", "items": [_encode(item) for item in value]}
    if _isinstance(value, _dict):
        return {"type": "dict", "items": [[_encode(k), _encode(v)] for k, v in value.items()]}
    return {"type": "opaque", "truthy": _bool(value)}

def _decode(node):
    kind = node["type"]
    if kind == "none":
        return None
    if kind == "bool":
        return _bool(node["value"])
    if kind == "int":
        return _int(node["value"])
    if kind == "float":
        return _float(node["value"])
    if kind == "complex":
        return _complex(_float(node["real"]), _float(node["imag"]))
    if kind == "str":
        return _str(node["value"])
    if kind == "bytes":
        return base64.b64decode(node["value"], validate=True)
    if kind == "list":
        return [_decode(item) for item in node["items"]]
    if kind == "tuple":
        return _tuple(_decode(item) for item in node["items"])
    if kind == "set":
        return {_decode(item) for item in node["items"]}
    if kind == "dict":
        return {_decode(k): _decode(v) for k, v in node["items"]}
    if kind == "opaque":
        return _OpaqueValue(_bool(node["truthy"]))
    raise ValueError(f"unknown MBPP value type: {kind!r}")
'''

_WORKER = _CODEC + '''\
import builtins
import json
import os
import sys
from pathlib import Path

trusted_exec = exec
trusted_compile = compile
caught = BaseException
json_loads = json.loads
json_dumps = json.dumps
emit = sys.stdout.write
flush = sys.stdout.flush
exit_now = os._exit
here = Path.cwd()
solution = here / "solution.py"
namespace = {"__name__": "__main__", "__file__": str(solution),
             "__builtins__": dict(vars(builtins))}
try:
    request = json_loads(sys.argv[1])
    source = solution.read_text(encoding="utf-8")
    trusted_exec(trusted_compile(source, str(solution), "exec"), namespace)
    function = namespace[request["name"]]
    value = function(*_decode(request["args"]), **_decode(request["kwargs"]))
    response = {"ok": True, "value": _encode(value)}
except caught as exc:
    response = {"ok": False, "error": f"{_type(exc).__name__}: {exc}"}
emit(WORKER_PREFIX + json_dumps(response, separators=(",", ":")) + "\\n")
flush()
exit_now(0)
'''

_DRIVER = _CODEC + '''\
import json
import os
import subprocess
import sys
from pathlib import Path

emit = sys.stdout.write
flush = sys.stdout.flush
exit_now = os._exit
here = Path(__file__).resolve().parent

def _remote_call(name, args, kwargs):
    request = json.dumps(
        {"name": name, "args": _encode(args), "kwargs": _encode(kwargs)},
        separators=(",", ":"),
    )
    proc = subprocess.run(
        [sys.executable, "-c", WORKER_SOURCE, request],
        cwd=here,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=CALL_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("candidate worker failed")
    reports = [line for line in proc.stdout.splitlines() if line.startswith(WORKER_PREFIX)]
    response = json.loads(reports[-1][len(WORKER_PREFIX):])
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "candidate call failed"))
    return _decode(response["value"])

class _RemoteFunction:
    def __init__(self, name):
        self.name = name

    def __call__(self, *args, **kwargs):
        return _remote_call(self.name, args, kwargs)

def _report(passed):
    emit(REPORT_PREFIX + str(passed) + "/" + str(len(TESTS)) + "\\n")
    flush()
    exit_now(0)

try:
    Path(__file__).resolve().unlink()
except OSError:
    _report(0)

namespace = {"__name__": "__main__"}
try:
    for statement in TEST_IMPORTS + REFERENCE_IMPORTS:
        exec(statement, namespace)
    for name in ENTRY_POINTS:
        namespace[name] = _RemoteFunction(name)
except BaseException:
    _report(0)

passed = 0
for assertion in TESTS:
    try:
        exec(assertion, namespace)
        passed += 1
    except BaseException:
        pass
_report(passed)
'''


def dataset_path(dataset_file: str | os.PathLike | None = None) -> Path:
    """Resolve an explicit, environment-provided, or cached sanitized MBPP dataset."""
    if dataset_file:
        return Path(dataset_file).expanduser()
    env = os.environ.get("PROTEUS_MBPP_PATH")
    if env:
        return Path(env).expanduser()
    cache = Path.home() / ".cache" / "proteus" / "mbpp" / "sanitized-mbpp.json"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with request.urlopen(DATA_URL, timeout=30) as response:
            payload = response.read()
        rows = json.loads(payload.decode("utf-8"))
        if not isinstance(rows, list):
            raise ValueError("sanitized MBPP download is not a JSON list")
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=cache.parent, delete=False) as tmp:
                tmp.write(payload)
                temp_path = Path(tmp.name)
            temp_path.replace(cache)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    return cache


def _records(path: Path) -> dict[str, dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row["task_id"]): row for row in rows}


def list_tasks(dataset_file: str | os.PathLike | None = None) -> list[str]:
    """Task IDs available in the sanitized dataset."""
    return sorted(_records(dataset_path(dataset_file)), key=int)


def _load(path: Path, task_id: int | str) -> dict:
    key = str(task_id)
    try:
        return _records(path)[key]
    except KeyError:
        raise KeyError(f"unknown MBPP task {key!r}; see proteus.bench.mbpp.list_tasks()") \
            from None


def _setup(ws: Path, spec: dict) -> None:
    (ws / "README.md").write_text(
        f"# MBPP task {spec['task_id']}\n\n{spec['prompt'].strip()}\n\n"
        "Implement your answer in `solution.py`.\n",
        encoding="utf-8",
    )
    (ws / "solution.py").write_text(
        '"""Implement the function described in README.md."""\n', encoding="utf-8"
    )


def _entry_points(spec: dict) -> list[str]:
    tree = ast.parse(spec["code"])
    return [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]


def _reference_imports(spec: dict) -> list[str]:
    source = spec["code"]
    tree = ast.parse(source)
    return [
        ast.get_source_segment(source, node) or ast.unparse(node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def _driver(spec: dict, report_prefix: str, worker_prefix: str) -> str:
    imports = list(spec.get("test_imports", []))
    tests = list(spec["test_list"])
    worker_source = f"WORKER_PREFIX = {worker_prefix!r}\n" + _WORKER
    return (
        f"TEST_IMPORTS = {imports!r}\nREFERENCE_IMPORTS = {_reference_imports(spec)!r}\n"
        f"TESTS = {tests!r}\n"
        f"ENTRY_POINTS = {_entry_points(spec)!r}\n"
        f"REPORT_PREFIX = {report_prefix!r}\nWORKER_PREFIX = {worker_prefix!r}\n"
        f"WORKER_SOURCE = {worker_source!r}\nCALL_TIMEOUT_S = {CALL_TIMEOUT_S}\n"
        + _DRIVER
    )


def _grade(ws: Path, spec: dict, name: str, *, sandbox=None) -> EvalResult:
    report_prefix = f"PROTEUS_MBPP_RESULT:{secrets.token_hex(16)}:"
    worker_prefix = f"PROTEUS_MBPP_VALUE:{secrets.token_hex(16)}:"
    driver = ws / "_grade.py"
    driver.write_text(_driver(spec, report_prefix, worker_prefix), encoding="utf-8")
    try:
        from proteus.bench.sandbox import run_python

        proc = run_python(
            ws, "_grade.py", timeout_s=GRADE_TIMEOUT_S, sandbox=sandbox, isolated=True
        )
    except subprocess.TimeoutExpired:
        return EvalResult(
            name=name,
            score=0.0,
            passed=False,
            detail=f"grading timed out after {GRADE_TIMEOUT_S}s",
        )
    finally:
        driver.unlink(missing_ok=True)

    expected = len(spec["test_list"])
    stdout = getattr(proc, "stdout", "")
    stderr = getattr(proc, "stderr", "")
    stdout = stdout if isinstance(stdout, str) else ""
    stderr = stderr if isinstance(stderr, str) else ""
    try:
        reports = [line for line in stdout.splitlines() if line.startswith(report_prefix)]
        passed_raw, total_raw = reports[-1][len(report_prefix):].split("/", 1)
        passed, total = int(passed_raw), int(total_raw)
        if getattr(proc, "returncode", 1) != 0 or total != expected or not 0 <= passed <= total:
            raise ValueError("invalid MBPP grader counts")
    except (IndexError, TypeError, ValueError):
        diagnostic = (stderr or stdout)[-200:]
        return EvalResult(
            name=name,
            score=0.0,
            passed=False,
            detail=f"grader produced no report: {diagnostic}",
        )

    return EvalResult(
        name=name,
        score=passed / total if total else 0.0,
        passed=(total > 0 and passed == total),
        detail=f"{passed}/{total} tests pass",
    )


def mbpp_task(task_id: int | str, dataset_file: str | os.PathLike | None = None) -> BenchTask:
    """Create one sanitized MBPP problem as a ``BenchTask``."""
    spec = _load(dataset_path(dataset_file), task_id)
    name = f"mbpp:{spec['task_id']}"
    return BenchTask(
        id=name,
        goal_text=(
            spec["prompt"].strip()
            + "\n\nImplement the solution in `task/solution.py`; official tests are held out."
        ),
        setup=lambda ws, s=spec: _setup(ws, s),
        grade=lambda ws, sandbox=None, s=spec, n=name: _grade(ws, s, n, sandbox=sandbox),
    )
