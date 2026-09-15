# A NESTED class sub-pattern that carries only a CONDITION
# (`case Outer(inner=Inner(v=1)):`) composes into the arm's condition exactly
# like a flat field test, so the tiers whose emit passes no base-name map --
# the Optional chain and the polymorphic tier -- admit it. A nested
# sub-pattern that would BIND a name still rejects there, since the binding
# would be spelled relative to a base only the record tiers thread
# (tests/cases/match/error_opt_nested_field_bind). Sections: Optional chain,
# polymorphic subject, the record tier that already had it, method, generator.
from typing import Iterator, Optional, Protocol

from tpy import Own, dynamic, int32


class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Outer:
    inner: Inner

    def __init__(self, inner: Own[Inner]) -> None:
        self.inner = inner


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Collar:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag


class Dog(Pet):
    collar: Collar

    def __init__(self, collar: Own[Collar]) -> None:
        self.collar = collar

    def speak(self) -> str:
        return "woof"


class Cat(Pet):
    def speak(self) -> str:
        return "meow"


def opt_chain(o: Optional[Outer]) -> str:
    # The None arm is not a prefix, so this takes the Optional CHAIN.
    match o:  # tpyc: ok
        case Outer(inner=Inner(v=1)):
            return "one"
        case None:
            return "none"
        case _:
            return "other"


def poly(p: Pet) -> str:
    match p:  # tpyc: ok
        case Dog(collar=Collar(tag="x")):
            return "x-dog"
        case _:
            return "other"


def record(o: Outer) -> str:
    # The record tier threads the base-name map, so it had this already.
    match o:  # tpyc: ok
        case Outer(inner=Inner(v=1)):
            return "one"
        case _:
            return "other"


class Kennel:
    resident: Outer

    def __init__(self, resident: Own[Outer]) -> None:
        self.resident = resident

    def label(self) -> str:
        match self.resident:  # tpyc: ok
            case Outer(inner=Inner(v=1)):
                return "one"
            case _:
                return "other"


def steps(o: Outer) -> Iterator[int32]:
    # Generator position: the record chain, which the dispatch hook admits
    # (the Optional tiers are not hook-admitted -- their own row).
    match o:  # tpyc: ok
        case Outer(inner=Inner(v=1)):
            yield 1
        case _:
            yield 2
    yield 9


def main() -> None:
    print("opt_chain:", opt_chain(Outer(Inner(1))), opt_chain(None),
          opt_chain(Outer(Inner(7))))
    print("poly:", poly(Dog(Collar("x"))), poly(Dog(Collar("y"))), poly(Cat()))
    print("record:", record(Outer(Inner(1))), record(Outer(Inner(7))))
    print("method:", Kennel(Outer(Inner(1))).label(),
          Kennel(Outer(Inner(7))).label())
    for v in steps(Outer(Inner(1))):
        print("gen_one:", v)
    for v in steps(Outer(Inner(7))):
        print("gen_other:", v)


main()
