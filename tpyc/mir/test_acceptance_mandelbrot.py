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
        yield bodies, mir.workspace.summaries


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
  %1: int32 readonly global main::MAX_ITER
  %2: str readonly-ref global main::CHARS
  %3: int readonly-ref temporary residence=r0
  %4: int32 temporary residence=r0
  %5: bool temporary residence=r0
  %6: str owned-storage temporary duration=r1 residence=r1
  %7: str readonly-ref temporary residence=r1
  %8: int32 temporary residence=r1
  %9: char temporary residence=r1
  %10: float local log_val residence=r0
  %11: float temporary residence=r0
  %12: int readonly-ref temporary residence=r0
  %13: float temporary residence=r0
  %14: float temporary residence=r0
  %15: float temporary residence=r0
  %16: int32 temporary residence=r0
  %17: float temporary residence=r0
  %18: float temporary residence=r0
  %19: float temporary residence=r0
  %20: int owned-storage local char_idx duration=body residence=r0
  %21: float temporary residence=r0
  %22: str readonly-ref temporary residence=r0
  %23: int32 temporary residence=r0
  %24: int32 temporary residence=r0
  %25: int32 temporary residence=r0
  %26: float temporary residence=r0
  %27: str owned-storage temporary duration=body residence=r0
  %28: str readonly-ref temporary residence=r0
  %29: int readonly-ref temporary residence=r0
  %30: int32 temporary residence=r0
  %31: char temporary residence=r0
region r0 parent=body entry=bb0
region r1 parent=r0 entry=bb1
region r2 parent=r0 entry=bb2
bb0:
  %3 = borrow (*%0) @ 26:7
  %4 = read %1 @ 26:16
  %5 = %3 >= %4 @ 26:7
  branch %5 -> bb1, bb2 @ 26:4
bb1:
  %7 = borrow (*%2) @ 27:15
  %8 = 0 @ 27:21
  %9 = op getitem (%7, %8) may-raise @ 27:15
  %6 = op coerce:char_to_str (%9) may-raise [initialize_region] @ 27:15
  return %6 @ 27:8
bb2:
  goto bb3 @ 26:4
bb3:
  %11 = 1.0 @ 29:23
  %12 = borrow (*%0) @ 29:29
  %13 = op + (%11, %12) may-raise @ 29:23
  %14 = call stub math.log[float](%13) [pure, reader, may-raise] @ 29:14
  %15 = 1.0 @ 29:47
  %16 = read %1 @ 29:53
  %17 = op + (%15, %16) may-raise @ 29:47
  %18 = call stub math.log[float](%17) [pure, reader, may-raise] @ 29:38
  %19 = op div (%14, %18) may-raise @ 29:14
  %10 = read %19 @ 29:4
  %21 = read %10 @ 30:19
  %22 = borrow (*%2) @ 30:34
  %23 = call stub tpy._builtins._funcs.len[Sized](%22) [pure, reader, may-raise] @ 30:30
  %24 = 1 @ 30:43
  %25 = op - (%23, %24) may-raise @ 30:30
  %26 = op * (%21, %25) may-raise @ 30:19
  %20 = call stub builtins.int.__init__[float](%26) [pure, reader, may-raise] [initialize_once] @ 30:4
  %28 = borrow (*%2) @ 31:11
  %29 = borrow %20 @ 31:17
  %30 = op coerce:bigint_narrow (%29) may-raise @ 31:11
  %31 = op getitem (%28, %30) may-raise @ 31:11
  %27 = op coerce:char_to_str (%31) may-raise [initialize_once] @ 31:11
  return %27 @ 31:4
"""


def test_iter_to_char_dump(program) -> None:
    # Stub calls summarized from their contracts, a str global lent to `len`, a
    # fresh BigInt from `int(float)` and argument expressions evaluated before each call.
    bodies, _ = program
    assert dump_function(bodies["iter_to_char"]) == ITER_TO_CHAR
