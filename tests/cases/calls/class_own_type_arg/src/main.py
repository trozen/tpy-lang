# A type argument of a class written in TPy drops a non-plain `Own[X]`
# (BUGS.md#class-own-type-arg-method-result-ill-formed):
# `C[Own[P]]` is `C[P]`, warned where it is written, so every section lends
# its element as `C[P]` does -- a write through `get()` reaches the list.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32
from fixtures import C, P, mk
import part_declared
import part_positions


def by_param(c: C[Own[P]]) -> int32:  # tpyc: warning(/`Own\[P\]` in a type argument of class 'C' has no effect yet; write `C\[P\]`/)
    h = c.get()
    h.x = 9
    return c.items[0].x


def by_return() -> Own[C[Own[P]]]:  # tpyc: warning(/has no effect yet/)
    c = C[P]()
    c.items.append(P(1))
    return c


def pending(f: Callable[[int32], Own[P]]) -> None:
    # a construction completed by `fill`: `f`'s `Own[P]` result meets
    # `fill`'s `Own[T]`, so it binds `T = P`
    c = C()
    c.fill(f, 3)  # tpyc: ok
    h = c.get()
    h.x = 9
    print("pending", c.items[0].x)


def class_method(f: Callable[[int32], Own[P]]) -> None:
    # a class method whose class `T` the callable's `Own[P]` result binds to
    # `P` through `build`'s `Own[T]`
    c = C.build(f, 4)  # tpyc: ok
    h = c.get()
    h.x = 9
    print("classmethod", c.items[0].x)


def main() -> None:
    # a written constructor
    c = C[Own[P]]()  # tpyc: warning(/`Own\[P\]` in a type argument of class 'C' has no effect yet; write `C\[P\]`/)
    c.items.append(P(1))
    h = c.get()
    h.x = 9
    print("ctor", c.items[0].x)
    # a parameter annotation
    d = C[P]()
    d.items.append(P(1))
    print("param", by_param(d))
    # a return annotation
    r = by_return()
    g = r.get()
    g.x = 9
    print("return", r.items[0].x)
    # a local annotation
    loc: C[Own[P]] = C()  # tpyc: warning(/has no effect yet/)
    loc.items.append(P(1))
    lh = loc.get()
    lh.x = 9
    print("local", loc.items[0].x)
    pending(mk)
    class_method(mk)
    part_positions.main()
    part_declared.main()


main()
