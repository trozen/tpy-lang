# An `Own[T]` parameter handed to a `T` parameter of the same class, and a
# nested def capturing a `T` parameter: at `Own[P]` the last use moves on, as
# the twin's `std::move`; a read before it copies, warned at the class.
# A spelled `Lid[T](v)` handing its `Own[T]` on.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32
from fixtures import P


class Inner[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, value: T) -> None:
        self.items.append(value)  # tpyc: warning(/may copy T into owned storage/)


class Outer[T]:
    _in: Inner[T]

    def __init__(self) -> None:
        self._in = Inner[T]()

    def put_own(self, value: Own[T]) -> None:
        # the last use moves on at `Own[P]`: no copy reported for this line
        self._in.put(value)

    def put_twice(self, value: Own[T]) -> None:
        # the first read copies at `Own[P]`, the last moves on
        self._in.put(value)
        self._in.put(value)


class Keep[T]:
    def __init__(self) -> None:
        pass

    def get_after(self, x: T) -> Callable[[], Own[T]]:
        def inner() -> Own[T]:
            return x  # tpyc: warning(/may copy T into owned storage/)
        # read after the closure: at `Own[P]` the closure holds a copy
        ys = [x]  # tpyc: warning(/may copy T into owned storage/)
        print("keep-after", len(ys))
        return inner

    def get_last(self, x: T) -> Callable[[], Own[T]]:
        # the capture is the parameter's last use: taken over at `Own[P]`
        def inner() -> Own[T]:
            return x  # tpyc: warning(/may copy T into owned storage/)
        return inner


class Lid[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)


def take_lid[T](c: Lid[T]) -> int32:
    return 1


# never consumed: BUGS.md#own-t-never-consumed-per-body
def lid_arg[T](v: Own[T]) -> int32:  # tpyc: warning(/never consumed/)
    # the spelled construction binds `take_lid`'s `T` to `T` itself: at
    # `Own[P]` the argument moves into the `Lid`, nothing is copied
    return take_lid(Lid[T](v))  # tpyc: ok


def main() -> None:
    print("lid-own", lid_arg[Own[P]](P(2)))  # tpyc: ok
    o = Outer[Own[P]]()  # tpyc: warning(/copies P into owned storage in 'Outer.put_twice' \(part_handover.py:33\)/)
    p = P(1)
    o.put_own(p)  # tpyc: warning(/copies P into owned storage; use copy/)
    p.n = 5
    print("forward-last", o._in.items[0].n, p.n)
    o.put_twice(P(2))
    o._in.items[1].n = 20
    print("forward-twice", o._in.items[1].n, o._in.items[2].n)
    k = Keep[Own[P]]()  # tpyc: warning(/copies P into escaping closure 'inner' in 'Keep.get_after' \(part_handover.py:42\)/)
    f = k.get_after(P(6))
    print("capture-after", f().n)
    # each call returns its own copy of what the closure took over
    g = k.get_last(P(8))
    r = g()
    r.n = 80
    print("capture-last", g().n, r.n)
