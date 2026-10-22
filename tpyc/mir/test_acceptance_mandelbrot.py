"""Every function of the mandelbrot example lowers to MIR, every analysis
completes, and `main` consumes KNOWN summaries of the two user functions it
calls. The program mixes BigInt, str, a str global, stub calls (`math.log`,
`len`, `int(float)`, `time.time`) and printing."""

import pytest

from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from .call_contract import MIRGlobalId, MIRSummaryState
from .collect import call_definitions
from .dump import dump_function
from .dependencies import analyze_dependencies
from .liveness import analyze_liveness
from .nodes import MIRAssign, MIRCall, MIRFunction, MIRNotCovered
from .retention import analyze_retention
from .scope_lifetime import inspect_scope_lifetimes
from .storage import analyze_storage

# A copy of tpy-examples basics/mandelbrot.py.
SOURCE = '''\
import math
import time

WIDTH = 100
HEIGHT = 50
MAX_ITER = 1000

# Mandelbrot set bounds
X_MIN, X_MAX = -2.5, 1.0
Y_MIN, Y_MAX = -1.25, 1.25

# ASCII gradient from dark to light
CHARS = " .,:;+*?%S#@"


def mandelbrot(cx: float, cy: float) -> int:
    x, y = 0.0, 0.0
    for i in range(MAX_ITER):
        if x * x + y * y > 4.0:
            return i
        x, y = x * x - y * y + cx, 2.0 * x * y + cy
    return MAX_ITER


def iter_to_char(iters: int) -> str:
    if iters >= MAX_ITER:
        return CHARS[0]
    # Logarithmic scaling spreads low iteration counts across more characters
    log_val = math.log(1.0 + iters) / math.log(1.0 + MAX_ITER)
    char_idx = int(log_val * (len(CHARS) - 1))
    return CHARS[char_idx]


def main():
    t0 = time.time()
    for row in range(HEIGHT):
        cy = Y_MAX - (Y_MAX - Y_MIN) * row / HEIGHT
        for col in range(WIDTH):
            cx = X_MIN + (X_MAX - X_MIN) * col / WIDTH
            iters = mandelbrot(cx, cy)
            print(iter_to_char(iters), end="")
        print("")
    print("elapsed[ms]:", (time.time() - t0) * 1000)


if __name__ == "__main__":
    main()
'''


@pytest.fixture(scope="module")
def program():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        bodies = {body.declaration.split("@")[0]: mir.workspace.bodies[body]
                  for body, _ in call_definitions(ctx, entry.name)}
        result = bodies, mir.workspace.summaries
    return result


@pytest.mark.parametrize("name", ["mandelbrot", "iter_to_char", "main"])
def test_every_function_lowers_and_every_analysis_completes(program, name: str) -> None:
    bodies, _ = program
    fn = bodies[name]
    assert isinstance(fn, MIRFunction), fn
    liveness = analyze_liveness(fn)
    dependencies = analyze_dependencies(fn, liveness)
    events = analyze_storage(fn)
    assert not isinstance(dependencies, MIRNotCovered) and not isinstance(events, MIRNotCovered)
    assert analyze_retention(fn, liveness, dependencies, events).conflicts == ()
    assert inspect_scope_lifetimes(fn).conflicts == ()


def test_main_consumes_known_summaries(program) -> None:
    bodies, summaries = program
    for name in ("mandelbrot", "iter_to_char"):
        result = summaries[th.THIRFunctionIdentity("main", name)]
        assert result.state is MIRSummaryState.KNOWN, result.reason
        assert result.summary.normal_return_only is False
    assert summaries[th.THIRFunctionIdentity("main", "mandelbrot")].summary.global_reads == frozenset(
        {MIRGlobalId("main", "MAX_ITER")})
    assert summaries[th.THIRFunctionIdentity("main", "iter_to_char")].summary.global_reads == frozenset(
        {MIRGlobalId("main", "MAX_ITER"), MIRGlobalId("main", "CHARS")})
    calls = [stmt.value for block in bodies["main"].blocks for stmt in block.statements
             if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCall)
             and isinstance(stmt.value.summary.callee, th.THIRResolvedCallee)]
    assert sorted(c.summary.callee.identity.name for c in calls) == ["iter_to_char", "mandelbrot"]
    # Each call consumes the finalized summary itself, with its exit and global facts.
    for call in calls:
        assert call.summary is summaries[call.summary.callee.identity].summary
        assert call.may_raise and call.summary.global_reads


ITER_TO_CHAR = """\
fn main::iter_to_char@25:0 -> str
entry bb0
exceptional exits
  %0: int readonly-ref parameter iters
  %1: int readonly-ref temporary residence=r0
  %2: int32 temporary residence=r0
  %3: int32 readonly global main::MAX_ITER
  %4: bool temporary residence=r0
  %5: str readonly-ref temporary residence=r1
  %6: str readonly-ref global main::CHARS
  %7: int32 temporary residence=r1
  %8: char temporary residence=r1
  %9: str owned-storage temporary duration=r1 residence=r1
  %10: float temporary residence=r0
  %11: int readonly-ref temporary residence=r0
  %12: float temporary residence=r0
  %13: float temporary residence=r0
  %14: float temporary residence=r0
  %15: int32 temporary residence=r0
  %16: float temporary residence=r0
  %17: float temporary residence=r0
  %18: float temporary residence=r0
  %19: float local log_val residence=r0
  %20: float temporary residence=r0
  %21: str readonly-ref temporary residence=r0
  %22: int32 temporary residence=r0
  %23: int32 temporary residence=r0
  %24: int32 temporary residence=r0
  %25: float temporary residence=r0
  %26: int owned-storage local char_idx duration=body residence=r0
  %27: str readonly-ref temporary residence=r0
  %28: int readonly-ref temporary residence=r0
  %29: int32 temporary residence=r0
  %30: char temporary residence=r0
  %31: str owned-storage temporary duration=body residence=r0
region r0 parent=body entry=bb0
region r1 parent=r0 entry=bb1
region r2 parent=r0 entry=bb2
bb0:
  %1 = borrow (*%0) @ 26:7
  %2 = read %3 @ 26:16
  %4 = %1 >= %2 @ 26:7
  branch %4 -> bb1, bb2 @ 26:4
bb1:
  %5 = borrow (*%6) @ 27:15
  %7 = 0 @ 27:21
  %8 = op getitem (%5, %7) may-raise @ 27:15
  %9 = op coerce:char_to_str (%8) may-raise [initialize] @ 27:8
  return %9 @ 27:8
bb2:
  goto bb3 @ 26:4
bb3:
  %10 = 1.0 @ 29:23
  %11 = borrow (*%0) @ 29:29
  %12 = op + (%10, %11) may-raise @ 29:23
  %13 = call stub math.log[float](%12) [pure, reader, may-raise] @ 29:14
  %14 = 1.0 @ 29:47
  %15 = read %3 @ 29:53
  %16 = op + (%14, %15) may-raise @ 29:47
  %17 = call stub math.log[float](%16) [pure, reader, may-raise] @ 29:38
  %18 = op div (%13, %17) may-raise @ 29:14
  %19 = read %18 @ 29:4
  %20 = read %19 @ 30:19
  %21 = borrow (*%6) @ 30:34
  %22 = call stub tpy._builtins._funcs.len[Sized](%21) [pure, reader, may-raise] @ 30:30
  %23 = 1 @ 30:43
  %24 = op - (%22, %23) may-raise @ 30:30
  %25 = op * (%20, %24) may-raise @ 30:19
  %26 = call stub builtins.int.__init__[float](%25) [pure, reader, may-raise] [initialize] @ 30:4
  %27 = borrow (*%6) @ 31:11
  %28 = borrow %26 @ 31:17
  %29 = op coerce:bigint_narrow (%28) may-raise @ 31:11
  %30 = op getitem (%27, %29) may-raise @ 31:11
  %31 = op coerce:char_to_str (%30) may-raise [initialize] @ 31:4
  return %31 @ 31:4
"""


def test_iter_to_char_dump(program) -> None:
    # Stub calls summarized from their contracts, a str global lent to `len`, a
    # fresh BigInt from `int(float)` and argument expressions evaluated before each call.
    bodies, _ = program
    assert dump_function(bodies["iter_to_char"]) == ITER_TO_CHAR
