# functools -- higher-order tools (pure TPy).
#
# Surface compared to CPython:
#   - reduce(func, a, initial): 3-arg form only.
#
# Gaps vs. CPython (tracked in STDLIB_ROADMAP.md):
#   - No 2-arg `reduce(func, a)` (uses `a[0]` as seed). Blocked on an overload-
#     resolution sema bug: when a function is overloaded and one parameter is
#     typed `Fn[...]`, neither named-function refs nor lambdas resolve against
#     the `Fn` context. See BUGS.md "Overload resolution + Fn param: named
#     functions and lambdas don't resolve". Once that's fixed, add the 2-arg
#     overload back (raises on empty input).
#   - Higher-arity / closure-heavy items (partial, lru_cache, singledispatch,
#     cached_property, partialmethod) are not yet available.
# tpy: cpp_namespace("tpystd::functools")
from typing import Iterable
from tpy import Fn, Own, copy


def reduce[T, U](func: Fn[[U, T], U], a: Iterable[T], initial: U) -> Own[U]:
    acc: U = copy(initial)
    for x in a:
        acc = func(acc, x)
    return acc
