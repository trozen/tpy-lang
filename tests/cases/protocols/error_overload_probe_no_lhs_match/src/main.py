# Regression: when NO overload candidate's return matches the LHS hint,
# the probe must fall back to unhinted arg analysis. The LHS hint is
# informational and shouldn't constrain overload resolution when no
# candidate returns the right shape.
#
# Pins the fallback path in `candidate_arg_hints` (returns None for all
# candidates -> `_probe_analyze_args` / `_probe_analyze_method_args`
# fall through to `[analyze_expr(arg) for arg in expr.args]`). The
# resulting call should fail (or succeed) on its OWN merits via
# resolve_overload + type-checking, not via LHS-driven bias toward a
# specific overload's arg shape.
#
# Scenario: two overloads return Int32 and Float32 respectively; LHS
# hint is `str`. Neither matches -> 0-LHS-matching candidates -> probe
# falls back. resolve_overload then picks the Int32 overload from the
# arg type, returns Int32, and the str = Int32 assignment fails with a
# type mismatch -- the error surfaces at assignment, NOT during the
# overload probe.
from typing import overload
from tpy import Int32, Float32


@overload
def f(x: Int32) -> Int32:
    return x


@overload
def f(x: Float32) -> Float32:
    return x


def main() -> None:
    s: str = f(Int32(5))  # tpyc: error(/Type mismatch.*got Int32/)
    print(s)


main()
