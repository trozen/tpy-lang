# A marker isinstance on a written protocol parameter tests the argument's type,
# not the reference the forwarding parameter deduces for an lvalue.
from typing import Protocol
from tpy import Copyable, Default, int32, nocopy


class HasN(Protocol):
    def get(self) -> int32: ...


@nocopy
class Pinned:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def get(self) -> int32:
        self.n += 1
        return self.n


class Plain:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def get(self) -> int32:
        self.n += 1
        return self.n


# `HasN.get` is not @readonly, so `p` is written.
def copyable(p: HasN) -> None:
    # a @nocopy reference record is not Copyable.
    # the warning is right: Copyable is undecorated because CPython's isinstance raises against it
    if isinstance(p, Copyable):  # tpyc: warning(/TPy-only/)
        print("copyable", p.get())
    else:
        print("not copyable", p.get())


def default(p: HasN) -> None:
    # a default-constructible reference record is Default.
    # the warning is right: Default is undecorated because CPython's isinstance raises against it
    if isinstance(p, Default):  # tpyc: warning(/TPy-only/)
        print("default", p.get())
    else:
        print("not default", p.get())


def main() -> None:
    pin = Pinned()
    copyable(pin)
    plain = Plain()
    copyable(plain)
    default(plain)
    # the argument was bound in place: the callee's writes are visible.
    print("after", pin.n, plain.n)


main()
