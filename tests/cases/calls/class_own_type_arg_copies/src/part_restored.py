# Positions forming a class at `Own[P]` -- a parameter, a result, a local, a
# tuple, a recursive alias with a match arm, a field spelled before its
# class, its own method, a nested def -- each reports its members' copies.
from __future__ import annotations
from tpy import Own, int32
from fixtures import C, P


class User:
    # a field spelled before its class is declared
    held: Later[Own[P]]  # tpyc: warning(/copies P into owned storage in 'Later.get' \(part_restored.py:24\)/)

    def __init__(self, held: Own[Later[Own[P]]]) -> None:  # tpyc: warning(/copies P into owned storage in 'Later.get' \(part_restored.py:24\)/)
        self.held = held


class Later[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def get(self) -> T:
        return self.items[0]

    def peek_own(self, other: Later[Own[P]]) -> int32:  # tpyc: warning(/copies P into owned storage in 'Later.get' \(part_restored.py:24\)/)
        # the class at `Own[P]` in its own method
        h = other.get()
        h.n = 9
        return other.items[0].n


type Tree[T] = C[T] | list[Tree[T]]


type CP = C[Own[P]]  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)


# an alias of the class at a parameter forms it once
def by_cp(c: CP) -> int32:  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    h = c.get()
    h.n = 9
    return c.items[0].n


# the alias's body forms `C[Own[P]]`
def by_alias(t: Tree[Own[P]]) -> int32:  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    match t:
        case C() as c:
            h = c.get()
            h.n = 9
            return c.items[0].n
        case _:
            return 0


def outer() -> int32:
    def inner(c: C[Own[P]]) -> int32:  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
        h = c.get()
        h.n = 9
        return c.items[0].n
    c = C[Own[P]]()  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    c.items.append(P(1))
    return inner(c)


def by_param(c: C[Own[P]]) -> int32:  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    h = c.get()
    h.n = 9
    return c.items[0].n


def by_return() -> Own[C[Own[P]]]:  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    c = C[Own[P]]()  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    c.items.append(P(1))
    return c


def main() -> None:
    d = C[Own[P]]()  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    d.items.append(P(1))
    print("param", by_param(d))
    r = by_return()
    g = r.get()
    g.n = 9
    print("return", r.items[0].n)
    loc: C[Own[P]] = C()  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    loc.items.append(P(1))
    lh = loc.get()
    lh.n = 9
    print("local", loc.items[0].n)
    t: tuple[C[Own[P]], int32] = (C[Own[P]](), 1)  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    t[0].items.append(P(1))
    th = t[0].get()
    th.n = 9
    print("tuple", t[0].items[0].n, t[1])
    a = C[Own[P]]()  # tpyc: warning(/copies P into owned storage in 'C.get' \(fixtures.py:47\)/)
    a.items.append(P(2))
    print("alias", by_alias(a), a.items[0].n)
    print("alias-cp", by_cp(a), a.items[0].n)
    u = User(Later[Own[P]]())  # tpyc: warning(/copies P into owned storage in 'Later.get' \(part_restored.py:24\)/)
    u.held.items.append(P(3))
    hh = u.held.get()
    hh.n = 30
    print("field-before", u.held.items[0].n)
    lt = Later[Own[P]]()  # tpyc: warning(/copies P into owned storage in 'Later.get' \(part_restored.py:24\)/)
    lt.items.append(P(4))
    print("own-method", lt.peek_own(lt), lt.items[0].n)
    print("nested", outer())
