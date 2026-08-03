# An OWNED (rvalue) manager must be frame-homed whenever the target's field points
# INTO it, which is broader than "the field is a plain alias". Two families answer
# alias-vs-own by themselves and so take their own field kinds, but still point at
# the manager: a pointer-repr `Optional` (a pointer field) and an explicit
# `StrView` (a view field). Both are read here after a suspension the `with` body
# does not contain, which is where a case-block manager would already be dead.
from typing import Iterator

from tpy import Int32, StrView


class Box:
    def __init__(self, n: Int32):
        self.n = n


class Holder:
    box: Box

    def __init__(self, n: Int32):
        self.box = Box(n)

    def __enter__(self) -> "Box | None":
        return self.box

    def __exit__(self, et, ev, tb) -> None:
        pass


class Named:
    name: str

    def __init__(self, name: str):
        self.name = name

    def __enter__(self) -> StrView:
        return self.name

    def __exit__(self, et, ev, tb) -> None:
        pass


def gen() -> Iterator[Int32]:
    h = Holder(7)
    with h as m:
        pass
    with Named("managed-label") as label:
        pass
    yield 1
    if m is not None:
        m.n += 1
        yield m.n
        # Through the manager's handle: a copy would stay 7.
        yield h.box.n
    print(label)
    yield len(label)


def main() -> None:
    for v in gen():
        print(v)


main()
