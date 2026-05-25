# Regression: when the user writes more explicit type-args than any
# overload candidate accepts, sema should emit the specific "expects N
# type argument(s), got M" diagnostic, not the generic "No matching
# @overload" fallback. Pre-fix the user-overload path
# (`_analyze_user_function_call`) skipped pre-validation; all candidates
# were rejected by `infer_type_params_for_function` (len > type_params
# returns None), the structural-match fallback caught each candidate's
# own raise via `except SemanticError: continue`, and the user saw "No
# matching @overload" with no hint about the arity error. Post-fix
# pre-validates against `max(len(f.type_params) for f in generic)`
# before dispatching to the overload resolver, mirroring the builtin
# path at calls.py:3815.
from typing import overload
from tpy import Int32


@overload
def f[T](x: T) -> T:
    return x


@overload
def f[U, V](x: U, y: V) -> U:
    return x


def main() -> None:
    # 3 explicit type-args; max type-params across overloads is 2.
    r: Int32 = f[Int32, Int32, Int32](Int32(5))  # tpyc: error(/expects 2 type argument.*got 3/)
    print(r)


main()
