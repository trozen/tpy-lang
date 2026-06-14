# itertools -- iterator-combinator generators (pure TPy).
#
# Coverage and the gaps still blocked on compiler work (and the BUGS.md /
# TODO.md entries that gate them) are tracked in docs/STDLIB_ROADMAP.md.
# tpy: cpp_namespace("tpystd::itertools")
from typing import Iterator, Iterable, Optional
from tpy import Fn, Int32


def count(start: int = 0, step: int = 1) -> Iterator[int]:
    n = start
    while True:
        yield n
        n = n + step


# `times=None` is the unbounded form (CPython's `repeat(obj)`); a count of 0 or
# negative yields nothing, matching CPython (`range` of a non-positive count is
# empty).
def repeat[T](obj: T, times: Optional[Int32] = None) -> Iterator[T]:
    if times is None:
        while True:
            yield obj
    else:
        for _ in range(times):
            yield obj


def cycle[T](it: Iterable[T]) -> Iterator[T]:
    saved: list[T] = []
    for x in it:
        saved.append(x)
        yield x
    while len(saved) > 0:
        for y in saved:
            yield y


# Single-argument `islice(it, stop)` only; the `islice(it, start, stop[, step])`
# form needs a second overload (see docs/STDLIB_ROADMAP.md).
def islice[T](it: Iterable[T], stop: Int32) -> Iterator[T]:
    i: Int32 = 0
    for x in it:
        if i >= stop:
            break
        yield x
        i += 1


def takewhile[T](pred: Fn[[T], bool], it: Iterable[T]) -> Iterator[T]:
    for x in it:
        if not pred(x):
            break
        yield x


def dropwhile[T](pred: Fn[[T], bool], it: Iterable[T]) -> Iterator[T]:
    dropping = True
    for x in it:
        if dropping:
            if pred(x):
                continue
            dropping = False
        yield x


def filterfalse[T](pred: Fn[[T], bool], it: Iterable[T]) -> Iterator[T]:
    for x in it:
        if not pred(x):
            yield x
