"""TPY_CPYTHON_FAST: under CPython the fixed-width numbers become the plain
builtins, and a program that stays in range prints what exact mode prints."""

import os
import subprocess
import sys
from pathlib import Path

PROGRAM = """\
from tpy import Own, float32, int32, uint8, uint32
import tpy

class Acc:
    total: int32

    def __init__(self) -> None:
        self.total = 0

def fill(acc: Acc, xs: list[int32]) -> Own[Acc]:
    for x in xs:
        acc.total += x * 3
    return acc

def main() -> None:
    acc = fill(Acc(), [1, 2, 3, 40])
    print(acc.total, uint8(200) + 50, float32(0.5) * 3)
    print(tpy.int32 is int, tpy.float32 is float)
    # exact mode: a 32-bit shift keeps its operand's width, other arithmetic
    # is an int
    u = uint32(1)
    print(~(u << 1), type(u + 1).__name__)

main()
"""


def _run(tmp_path: Path, fast: bool) -> str:
    prog = tmp_path / "prog.py"
    prog.write_text(PROGRAM)
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    env.pop("TPY_CPYTHON_FAST", None)
    if fast:
        env["TPY_CPYTHON_FAST"] = "1"
    r = subprocess.run([sys.executable, str(prog)], env=env, cwd=tmp_path,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_fast_mode_binds_the_builtins_and_keeps_in_range_output(tmp_path: Path) -> None:
    exact = _run(tmp_path, fast=False)
    fast = _run(tmp_path, fast=True)
    assert exact.splitlines()[0] == fast.splitlines()[0] == "138 250 1.5"
    assert exact.splitlines()[1] == "False False"
    assert fast.splitlines()[1] == "True True"
    assert exact.splitlines()[2] == "4294967293 int"
    assert fast.splitlines()[2] == "-3 int"


IMPORT_PROBE = """\
import sys
before = set(sys.modules)
import tpy
loaded = set(sys.modules) - before
print("asyncio" in loaded, "inspect" in loaded, "CancelledError" in dir(tpy))
first = tpy.CancelledError  # the first read loads asyncio
from tpy import CancelledError
import asyncio
print(first is CancelledError is asyncio.CancelledError)
"""

STAR_PROBE = """\
from tpy import *
import asyncio
print(CancelledError is asyncio.CancelledError)
"""


def test_import_leaves_asyncio_and_inspect_unloaded(tmp_path: Path) -> None:
    # Every module `import tpy` keeps alive is walked by each later major GC.
    for fast in (False, True):
        env = dict(os.environ)
        env.pop("TPY_CPYTHON_FAST", None)
        if fast:
            env["TPY_CPYTHON_FAST"] = "1"
        r = subprocess.run([sys.executable, "-c", IMPORT_PROBE], env=env,
                           cwd=tmp_path, capture_output=True, text=True,
                           timeout=60)
        assert r.returncode == 0, r.stderr
        assert r.stdout.splitlines() == ["False False True", "True"], fast
        r = subprocess.run([sys.executable, "-c", STAR_PROBE], env=env,
                           cwd=tmp_path, capture_output=True, text=True,
                           timeout=60)
        assert r.stdout == "True\n", (fast, r.stderr)
