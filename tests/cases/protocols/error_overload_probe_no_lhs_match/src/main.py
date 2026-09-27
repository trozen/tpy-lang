# Regression: when NO overload candidate's return matches the LHS hint,
# the probe must fall back to unhinted arg analysis. The LHS hint is
# informational and shouldn't constrain overload resolution when no
# candidate returns the right shape.
#
# Pins the fallback path in `candidate_arg_hints` (returns None for all
# candidates -> `_probe_candidate_args`
# falls through to unhinted analysis of every argument). The
# resulting call should fail (or succeed) on its OWN merits via
# resolve_overload + type-checking, not via LHS-driven bias toward a
# specific overload's arg shape.
#
# Scenario: two overloads return int32 and float32 respectively; LHS
# hint is `str`. Neither matches -> 0-LHS-matching candidates -> probe
# falls back. resolve_overload then picks the int32 overload from the
# arg type, returns int32, and the str = int32 assignment fails with a
# type mismatch -- the error surfaces at assignment, NOT during the
# overload probe.
from tpy import int32, float32, dispatch


@dispatch
def f(x: int32) -> int32:
    return x


@dispatch
def f(x: float32) -> float32:
    return x


def main() -> None:
    s: str = f(int32(5))  # tpyc: error(/Type mismatch.*got int32/)
    print(s)


main()
