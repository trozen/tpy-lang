# Further positions of a class type argument at `Own[X]`: a generic class's
# method signature over its open `T`, a nested def, a tuple and an alias
# reaching the class, an inferred generic call, and the plain `Own[int32]`
# and builtin `list[Own[P]]` the rule leaves alone.
from __future__ import annotations
from tpy import Own, int32
from fixtures import C, P


class Hold[T]:
    def __init__(self) -> None:
        pass

    def give(self, c: Own[C[T]]) -> Own[C[Own[T]]]:  # tpyc: warning(/`Own\[T\]` in a type argument of class 'C' has no effect yet; write `C\[T\]`/)
        return c


type Tree[T] = C[T] | list[Tree[T]]


def by_alias(t: Tree[Own[P]]) -> int32:
    match t:
        case C() as c:
            h = c.get()
            h.x = 9
            return c.items[0].x
        case _:
            return 0


def outer(x: P) -> int32:
    def inner(c: C[Own[P]]) -> int32:  # tpyc: warning(/has no effect yet/)
        h = c.get()
        h.x = 9
        return c.items[0].x
    c = C[P]()
    # a borrowed parameter into the list: copied, as at any `list[P]`
    c.items.append(x)  # tpyc: warning(/copies P into owned storage/)
    return inner(c)


def make_c[T](x: T) -> Own[C[T]]:
    c = C[T]()
    c.items.append(x)  # tpyc: warning(/may copy T into owned storage/)
    return c


def main() -> None:
    # a method signature over its open `T`
    c = C[P]()
    c.items.append(P(1))
    d = Hold[P]().give(c)
    h = d.get()
    h.x = 9
    print("method", d.items[0].x)
    # a nested def's parameter
    print("nested", outer(P(1)))
    # a tuple holding the class
    t: tuple[C[Own[P]], int32] = (C(), 1)  # tpyc: warning(/has no effect yet/)
    t[0].items.append(P(1))
    th = t[0].get()
    th.x = 9
    print("tuple", t[0].items[0].x, t[1])
    # a generic alias whose body holds the class
    a = C[P]()
    a.items.append(P(1))
    print("alias", by_alias(a))
    # an inferred generic call: `T = P`
    m = make_c(P(1))
    mh = m.get()
    mh.x = 9
    print("inferred", m.items[0].x)
    # left alone: a plain `Own[int32]` and a builtin container
    xs = list[Own[P]]()  # tpyc: ok
    xs.append(P(1))
    xh = xs[0]
    xh.x = 9
    print("builtin", xs[0].x)
