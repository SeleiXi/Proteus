"""Shared source builders for benchmark parent/worker process isolation."""

from __future__ import annotations

from typing import Mapping


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
    raise ValueError(f"unknown isolated value type: {kind!r}")
'''

WORKER_SOURCE = _CODEC + '''\
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

DRIVER_SUPPORT_SOURCE = _CODEC + '''\
import json
import os
import subprocess
import sys
from pathlib import Path

caught = BaseException
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
    if not reports:
        raise RuntimeError("candidate worker produced no report")
    response = json.loads(reports[-1][len(WORKER_PREFIX):])
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "candidate call failed"))
    return _decode(response["value"])

class _RemoteFunction:
    def __init__(self, name):
        self.name = name

    def __call__(self, *args, **kwargs):
        return _remote_call(self.name, args, kwargs)

'''


def build_worker_source(worker_prefix: str) -> str:
    """Return a self-contained candidate worker with a private result prefix."""
    return f"WORKER_PREFIX = {worker_prefix!r}\n" + WORKER_SOURCE


def build_driver_source(
    *,
    report_prefix: str,
    worker_prefix: str,
    call_timeout_s: int,
    bindings: Mapping[str, object],
    body: str,
) -> str:
    """Bind trusted values around the common remote-call support and benchmark body."""
    worker = build_worker_source(worker_prefix)
    values = {
        **dict(bindings),
        "REPORT_PREFIX": report_prefix,
        "WORKER_PREFIX": worker_prefix,
        "WORKER_SOURCE": worker,
        "CALL_TIMEOUT_S": call_timeout_s,
    }
    header = "".join(f"{name} = {value!r}\n" for name, value in values.items())
    return header + DRIVER_SUPPORT_SOURCE + body
