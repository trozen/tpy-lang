# Call results at three sinks no other case reaches: a match subject that is
# a free call returning a borrowed record union, an Own[@dynamic protocol]
# method result (bound and discarded), and a borrowed `P | None` method
# result lifted into an Optional field (a warned copy, so the field does not
# alias the store's element -- the section reads it back unchanged).
from __future__ import annotations

from typing import Protocol
from tpy import dynamic, nocopy, readonly, Own


class Dog:
    name: str

    def __init__(self, n: str) -> None:
        self.name = n


class Cat:
    name: str

    def __init__(self, n: str) -> None:
        self.name = n


def choose(d: Dog) -> Dog | Cat:
    return d


def union_subject() -> None:
    d = Dog("rex")
    # The subject is the call itself; the arm binds alias `d`.
    match choose(d):  # tpyc: ok
        case Dog() as x:
            x.name = "max"
        case Cat() as y:
            y.name = "tom"
    print("union_subject:", d.name)


@dynamic
class Cloneable(Protocol):
    @readonly
    def replicate(self) -> Own[Cloneable]: ...

    @readonly
    def label(self) -> str: ...


# @nocopy: a silent copy of the owned payload would be a compile error.
@nocopy
class Sheep(Cloneable):
    gen: int

    def __init__(self, gen: int) -> None:
        self.gen = gen

    @readonly
    def replicate(self) -> Own[Cloneable]:
        return Sheep(self.gen + 1)

    @readonly
    def label(self) -> str:
        return "sheep" + str(self.gen)


def protocol_own_dyn() -> None:
    d: Cloneable = Sheep(1)
    # The owning handle lands bare in the decl and at statement position.
    x = d.replicate()  # tpyc: ok
    d.replicate()  # tpyc: ok
    print("protocol_own_dyn:", d.label(), x.label())


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Store:
    ps: list[P]

    def __init__(self) -> None:
        self.ps = [P(1), P(2)]

    def find(self, k: int) -> P | None:
        if k < len(self.ps):
            return self.ps[k]
        return None


class H:
    o: P | None

    def __init__(self) -> None:
        self.o = None


def ptr_opt_lift() -> None:
    s = Store()
    h = H()
    # The borrowed pointer result is lifted into the field's own storage.
    h.o = s.find(1)  # tpyc: warning(/copies P \| None into field/)
    o = h.o
    print("ptr_opt_lift:", o.v if o is not None else -1)


def main() -> None:
    union_subject()
    protocol_own_dyn()
    ptr_opt_lift()


main()
