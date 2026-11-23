# Class type arguments beside `C[Own[X]]` (calls/class_own_type_arg):
# `Box[P]` and a builtin `list[Own[P]]` stay as written, and a pending
# construction completed through a callable
# returning `Own[P]` binds `T = P`, so its element is lent as at `C[P]`.
# (A plain
# `C[Own[int32]]` passes sema but is not lowered:
# BUGS.md#class-plain-own-type-arg-rejects.)
from typing import Callable
from tpy import Own, int32
from tplib import Box


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class C[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, x: T) -> None:
        # the hedge every generic append of a borrowed `T` carries: at a
        # class `T` the element is copied in
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)

    def fill(self, f: Callable[[int32], Own[T]], k: int32) -> None:
        self.items.append(f(k))

    def get(self) -> T:
        return self.items[0]


def mk(k: int32) -> Own[P]:
    return P(k)


def pending(f: Callable[[int32], Own[P]]) -> None:
    # a pending construction completed by `fill`: `f`'s `Own[P]` result
    # meets `fill`'s `Own[T]`, so it binds `T = P`
    c = C()
    c.fill(f, 3)  # tpyc: ok
    h = c.get()
    h.x = 9
    print("class-pending", c.items[0].x)


def main() -> None:
    b = Box[P](P(1))  # tpyc: ok
    print("class-box", b.get().x)
    xs = list[Own[P]]()  # tpyc: ok
    xs.append(P(5))
    print("class-list-own", xs[0].x)
    pending(mk)
