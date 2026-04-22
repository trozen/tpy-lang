# functools -- higher-order tools (pure TPy).
#
# Surface compared to CPython:
#   - reduce(func, a, initial): 3-arg form only.
#
# Gaps vs. CPython (tracked in STDLIB_ROADMAP.md):
#   - reduce takes `list[T]` instead of `Iterable[T]` -- same list-literal /
#     protocol-conformance gap as math.prod, heapq.nsmallest (TODO.md:53).
#   - No 2-arg `reduce(func, a)` (uses `a[0]` as seed). Blocked on an overload-
#     resolution sema bug: when a function is overloaded and one parameter is
#     typed `Fn[...]`, neither named-function refs nor lambdas resolve against
#     the `Fn` context. See TODO.md "Overload resolution + Fn param: named
#     functions and lambdas don't resolve". Once that's fixed, add the 2-arg
#     overload back (raises on empty input).
#   - Higher-arity / closure-heavy items (partial, lru_cache, singledispatch,
#     cached_property, partialmethod) are not yet available.
#   - `reduce` on `str` accumulators silently produces garbage output: the
#     generic `U` is lowered as `std::string_view` (param form) rather than
#     `std::string` (storage form), and the lambda's owned-string result
#     becomes a dangling view. Use an explicit non-generic wrapper or switch
#     to Int/bytes until the compiler bug lands. See TODO.md "Generic U over
#     str drops lifetime".
# tpy: cpp_namespace("tpystd::functools")
from tpy import Fn, Own, copy


def reduce[T, U](func: Fn[[U, T], U], a: list[T], initial: U) -> Own[U]:
    acc: U = copy(initial)
    for x in a:
        acc = func(acc, x)
    return acc
