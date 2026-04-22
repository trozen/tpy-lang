# Regression guard: overload resolution must not let `type_conforms_to_protocol`
# cement the first-probed overload's element type on an empty list literal.
#
# Before the W2 fix, the int overload (listed first) was probed first and
# wrote coerced_element_type=int into the pending-list info as a side effect.
# When the float overload then became the actual winner, the empty list would
# stay typed as list[int], and C++ rejected the arg against Iterable<double>
# ("could not convert std::vector<int32_t> to std::span<const double>").
#
# With the fix: type_conforms_to_protocol is pure, and coerced_element_type
# is pinned only after overload resolution picks a winner (in
# `_typecheck_call_args` / `_check_and_coerce_args`).
from typing import Iterable, overload


@overload
def pick(xs: Iterable[int], y: int) -> str:
    return "int"


@overload
def pick(xs: Iterable[float], y: float) -> str:
    return "float"


def main() -> None:
    # y=1.0 disambiguates to the float overload. Before W2 fix, the empty
    # list was cemented as list[int] from the int probe and compilation
    # failed. After fix, empty list correctly types as list[float].
    print(pick([], 1.0))

    # Same pattern for builtin-overloads dispatch: `sum([])` routes through
    # _analyze_builtin_function_overloads, a different call path than pick().
    # Both paths now call _maybe_coerce_empty_list_to_protocol post-resolution.
    #
    # ORDER-DEPENDENT: empty list matches multiple `sum` overloads trivially
    # (Iterable[int], Iterable[float]); resolve_overload picks the first
    # listed. It happens to be Iterable[int] in lib/tpy/tpy/_builtins/_funcs.py,
    # so `sum([]) == 0` (int), matching CPython. If that stub is reordered
    # (float first), `sum([])` would silently return 0.0 instead -- a
    # CPython-compat regression. See TODO.md "Generic type param inference
    # rejects empty iterable literals" for the principled long-term fix
    # (default T via an AnyInt/Numeric lattice rather than stub ordering).
    print(sum([]))


main()
