# Regime C body-error surfacing: when 2+ Fn-bearing candidates are viable
# and the lambda body has a real type error (independent of which candidate
# is picked), the per-candidate trial saves the body error and surfaces it
# instead of a generic "no matching overload" message. Exercises the
# saved_dry_error path through _lambda_body_dry_run + _build_regime_c_evidence.
from tpy import Fn, int32, dispatch


@dispatch
def m[T, U](f: Fn[[T], U], xs: list[T]) -> int32:
    return int32(0)


@dispatch
def m[T, U](f: Fn[[T, T], U], xs: list[T]) -> int32:
    return int32(0)


def main() -> None:
    xs: list[int32] = [1, 2, 3]
    # The body `a + "x"` is invalid for T=int32 -- error surfaces from
    # the lambda body analysis trial, not the bare overload-mismatch path.
    print(m(lambda a: a + "x", xs))  # tpyc: error(/Invalid operand types for '\+': int32 and str/)


main()
