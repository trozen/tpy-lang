# itertools -- iterator-combinator generators (pure TPy).
#
# Coverage and the gaps still blocked on compiler work (and the BUGS.md /
# TODO.md entries that gate them) are tracked in docs/STDLIB_ROADMAP.md.
# tpy: cpp_namespace("tpystd::itertools")
from typing import Iterator, Iterable, Optional, overload
from tpy import Fn, int32


def count(start: int = 0, step: int = 1) -> Iterator[int]:
    n = start
    while True:
        yield n
        n = n + step


# Two public arms (CPython's `repeat(object)` / `repeat(object, times)`); the
# impl's `Optional` sentinel stays private behind the overloads, so
# `repeat(object, None)` is rejected the way CPython rejects a non-int `times`.
# The param is named `object` (shadowing the builtin) to match CPython's keyword
# surface (`repeat(object=...)`). A count of 0 or negative yields nothing.
@overload
def repeat[T](object: T) -> Iterator[T]: ...
@overload
def repeat[T](object: T, times: int32) -> Iterator[T]: ...
def repeat[T](object: T, times: Optional[int32] = None) -> Iterator[T]:
    if times is None:
        while True:
            yield object
    else:
        for _ in range(times):
            yield object


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
def islice[T](it: Iterable[T], stop: int32) -> Iterator[T]:
    i: int32 = 0
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
