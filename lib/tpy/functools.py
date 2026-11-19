# functools -- higher-order tools (pure TPy).
#
# Surface compared to CPython:
#   - Each form comes twice: a combiner returning one of its arguments
#     (`-> T`, a reference type) lends it back, as CPython returns that
#     object; a combiner building a new value (`-> Own[T]`, or any value
#     type) moves each result into the accumulator.
#   - reduce(func, a, initial): the owning form takes any Iterable[T]; the
#     lending form a list (a generator's step would not outlive the loop).
#   - reduce(func, a): list[T] only (random access); an empty list raises
#     TypeError, as CPython. TPy lacks CPython's iter/next + StopIteration,
#     so the 2-arg form can't accept arbitrary Iterable[T].
#   - total_ordering: re-exported from `_functools_macros` (sibling
#     macro module).
#
# Gaps tracked in STDLIB_ROADMAP.md.
__tpy__ = {"cpp_namespace": "tpystd::functools"}
from typing import Iterable
from tpy import Fn, Own, ReferenceType, copy, dispatch
from _functools_macros import total_ordering


@dispatch
def reduce[T, U: ReferenceType](func: Fn[[U, T], U], a: list[T], initial: U) -> U:
    acc = initial
    for i in range(len(a)):
        acc = func(acc, a[i])
    return acc


@dispatch
def reduce[T, U](func: Fn[[U, T], Own[U]], a: Iterable[T], initial: U) -> Own[U]:
    acc: U = copy(initial)
    for x in a:
        acc = func(acc, x)
    return acc


@dispatch
def reduce[T: ReferenceType](func: Fn[[T, T], T], a: list[T]) -> T:
    if len(a) == 0:
        raise TypeError("reduce() of empty iterable with no initial value")
    acc = a[0]
    for i in range(1, len(a)):
        acc = func(acc, a[i])
    return acc


@dispatch
def reduce[T](func: Fn[[T, T], Own[T]], a: list[T]) -> Own[T]:
    if len(a) == 0:
        raise TypeError("reduce() of empty iterable with no initial value")
    if len(a) == 1:
        return copy(a[0])
    acc = func(a[0], a[1])
    for i in range(2, len(a)):
        acc = func(acc, a[i])
    return acc
