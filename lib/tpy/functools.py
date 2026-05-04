# functools -- higher-order tools (pure TPy).
#
# Surface compared to CPython:
#   - reduce(func, a, initial): 3-arg form on Iterable[T].
#   - reduce(func, a): 2-arg form on list[T] (uses a[0] as seed; raises
#     on empty). Restricted to list rather than arbitrary Iterable
#     because TPy doesn't have CPython's iter/next + StopIteration
#     pattern; indexing a[0] needs random access.
#
# Gaps vs. CPython (tracked in STDLIB_ROADMAP.md):
#   - 2-arg reduce accepts list, not arbitrary Iterable.
#   - Higher-arity / closure-heavy items (partial, lru_cache, singledispatch,
#     cached_property, partialmethod) are not yet available.
# tpy: cpp_namespace("tpystd::functools")
from typing import Iterable, overload
from tpy import Fn, Own, copy


@overload
def reduce[T, U](func: Fn[[U, T], U], a: Iterable[T], initial: U) -> Own[U]:
    acc: U = copy(initial)
    for x in a:
        acc = func(acc, x)
    return acc


@overload
def reduce[T](func: Fn[[T, T], T], a: list[T]) -> Own[T]:
    if len(a) == 0:
        raise ValueError("reduce() of empty list with no initial value")
    acc: T = copy(a[0])
    for i in range(1, len(a)):
        acc = func(acc, a[i])
    return acc
