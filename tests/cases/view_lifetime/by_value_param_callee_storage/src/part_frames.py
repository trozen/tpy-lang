# A view of a by-value parameter used inside the function stays a view: in a
# block, in a generator declared before a loop, in a generator expression
# capturing the enclosing parameter.
from typing import Iterator
from tpy import StrView
from fixtures import V


def chars(s: StrView) -> Iterator[StrView]:
    yield s


def in_block(c: bool, x: V, y: V) -> None:
    if c:
        g = chars(x.name)  # tpyc: ok
    else:
        g = chars(y.name)
    for s in g:
        print("frames block:", s[:2])


def after_loop(v: V) -> None:
    for i in range(2):
        g = chars(v.name)  # tpyc: ok
    for s in g:
        print("frames after loop:", s[:2])


def captured(v: V) -> None:
    for s in (v.get() for _ in range(2)):  # tpyc: ok
        print("frames genexpr:", s[:2])


def main() -> None:
    in_block(True, V("a" * 20), V("b" * 20))
    after_loop(V("c" * 20))
    captured(V("d" * 20))
