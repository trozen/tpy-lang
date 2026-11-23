# A select handed to a generator call a loop head keeps is held in the
# frame like any other fresh argument: a ternary of a fresh f-string and a
# `str` parameter, of a literal and a fresh f-string, an `or` of fresh
# f-strings and of fresh lists, a ternary of two comprehensions.
from typing import Iterable, Iterator
from tpy import char, int32


def chars(xs: Iterable[char]) -> Iterator[char]:
    for x in xs:
        yield x


def each_t[T](it: Iterable[T]) -> Iterator[T]:
    for x in it:
        yield x


def gen_param(c: bool, s: str) -> Iterator[str]:
    # one arm a fresh f-string, the other a `str` parameter
    for ch in chars(f"{c}abc" if c else s):  # tpyc: ok
        yield str(ch)


def gen_lit(c: bool) -> Iterator[str]:
    # a literal arm and a fresh f-string arm
    for ch in chars("lit" if c else f"{c}0123456789012345678901234"):  # tpyc: ok
        yield str(ch)


def gen_or(p: str, n: int32) -> Iterator[str]:
    # an `or` of fresh f-strings, and of fresh lists at `Iterable[T]`
    for ch in chars(f"{p}" or f"b{n}0123456789012345678901234"):  # tpyc: ok
        yield str(ch)
    for x in each_t(list(range(n)) or [7, n]):  # tpyc: ok
        yield str(x)


def gen_comps(c: bool) -> Iterator[int32]:
    # a conditional between two comprehensions
    for y in each_t([x for x in range(3)] if c else [x * 2 for x in range(3)]):  # tpyc: ok
        yield y


def main() -> None:
    print("head select param", list(gen_param(True, "xy")),
          list(gen_param(False, "xy")))
    print("head select lit", "".join(gen_lit(True)), "".join(gen_lit(False)))
    print("head select or", "".join(gen_or("", 0)), "".join(gen_or("q", 2)))
    print("head select comps", list(gen_comps(True)), list(gen_comps(False)))
