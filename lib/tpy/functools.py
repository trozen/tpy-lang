# functools -- higher-order tools (pure TPy).
#
# Surface compared to CPython:
#   - reduce(func, a, initial): 3-arg form on Iterable[T].
#   - reduce(func, a): 2-arg form on list[T] only (random access; raises
#     on empty). TPy lacks CPython's iter/next + StopIteration, so the
#     2-arg form can't accept arbitrary Iterable[T].
#   - total_ordering: re-exported from `_functools_macros` (sibling
#     macro module).
#
# Gaps tracked in STDLIB_ROADMAP.md.
# tpy: cpp_namespace("tpystd::functools")
from typing import Iterable, overload
from tpy import Fn, Own, copy
from _functools_macros import total_ordering


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
