"""TPY_CPYTHON_FAST: under CPython the fixed-width numbers become the plain
builtins, and a program that stays in range prints what exact mode prints."""

import os
import subprocess
import sys
from pathlib import Path

PROGRAM = """\
from tpy import Own, float32, int32, uint8
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
