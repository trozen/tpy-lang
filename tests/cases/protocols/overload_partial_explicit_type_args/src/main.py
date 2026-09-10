# Regression: partial-explicit type args at an overloaded generic call
# site MUST merge into the seed (relaxed exact-length check at
# `_resolve_call_overloads` Regime A/B fn_generic branch). Pre-fix the
# guard required a full-arity type-args list; partial-explicit was
# silently skipped at the seed merge.
#
# To make the relaxation observable, the explicit T must be info that
# CANNOT come from arg evidence -- otherwise Phase-2 partial_inferred
# would derive it anyway. The Fn-typed param's input position contains
# T, no other arg/return uses T, so T is reachable only from the
# explicit. Without the merge, the lambda hint has T unresolved -> the
# Phase-2 has_unresolved check skips the hint -> lambda body analyzed
# unhinted -> compile error ("Lambda parameter types cannot be
# inferred"). Post-fix, the merge fires (T=Int32) and the lambda hint
# becomes Fn[[Int32], U] -> body analyzes under Int32 -> compiles.
from tpy import Int32, Fn, dispatch


@dispatch
def stub[T, U](fn: Fn[[T], U]) -> Int32:
    return Int32(0)


@dispatch
def stub(val: str) -> str:
    return val


def main() -> None:
    r: Int32 = stub[Int32](lambda x: x * Int32(2))
    print(r)


main()
