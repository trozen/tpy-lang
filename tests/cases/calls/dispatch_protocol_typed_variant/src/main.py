# A @dispatch method set holding a protocol-typed variant beside a concrete
# one builds: `c.m(d)` names the concrete variant (`m__Dog`) and `c.m(cat)`
# the protocol one, deduced at `Cat`. The Dog variant renames its argument
# (a protocol parameter cannot be written through yet:
# BUGS.md#protocol-param-method-mutation-inferred-const).
from typing import Protocol
from tpy import dispatch, int32, StrView


class Named(Protocol):
    def name(self) -> StrView: ...


class Dog:
    n: str

    def __init__(self) -> None:
        self.n = "dog"

    def name(self) -> StrView:
        return self.n

    def rename(self, n: str) -> None:
        self.n = n


class Cat:
    n: str

    def __init__(self) -> None:
        self.n = "cat"

    def name(self) -> StrView:
        return self.n

    def rename(self, n: str) -> None:
        self.n = n


class Clinic:
    def __init__(self) -> None:
        pass

    @dispatch
    def m(self, x: Dog) -> int32:
        x.rename("seen dog")
        return 1

    @dispatch
    def m(self, x: Named) -> int32:
        return 20 + len(x.name())


def main() -> None:
    c = Clinic()
    d = Dog()
    cat = Cat()
    # The concrete variant for a Dog; the protocol one for anything else Named.
    print("dog:", c.m(d), d.name())  # tpyc: ok
    print("cat:", c.m(cat), cat.name())  # tpyc: ok


main()
