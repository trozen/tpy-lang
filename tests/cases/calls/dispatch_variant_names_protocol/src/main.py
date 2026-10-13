# A free @dispatch set with protocol variants: each variant has its own C++
# name, so the call names the variant sema ranked first instead of leaving
# the choice to C++ overload resolution (template vs const concrete, or two
# unordered constrained templates). Under CPython the `dispatch` stub matches
# the protocol variants structurally, which the protocol-only and
# two-protocol sections exercise.
from typing import Iterable, Protocol
from tpy import String, dispatch, readonly


class Animal(Protocol):
    def name(self) -> str: ...


class Dog:
    def __init__(self, nm: str) -> None:
        self.nm = nm

    @readonly
    def name(self) -> String:
        return String(self.nm)


# --- concrete vs protocol: the concrete parameter renders `const Dog&`
# (declared first, as CPython's dispatch runs the first applicable variant;
# the other order is in calls/dispatch_variant_names_protocol_order) ---
@dispatch
def describe(x: Dog) -> str:
    return "dog " + x.name()


@dispatch
def describe(x: Animal) -> str:
    return "animal " + x.name()


def concrete_section() -> None:
    d = Dog("Rex")
    print("concrete:", describe(d))  # the Dog variant, not the template
    d.nm = "Max"
    print("concrete:", describe(d))


# --- protocol only: no concrete variant takes a Cat, so `describe__Animal`
# is the one that matches ---
class Cat:
    def __init__(self, nm: str) -> None:
        self.nm = nm

    @readonly
    def name(self) -> String:
        return String(self.nm)


def protocol_only_section() -> None:
    c = Cat("Tom")
    print("protocol only:", describe(c))  # tpyc: ok -- only the Animal variant takes a Cat
    c.nm = "Kit"
    print("protocol only:", describe(c))


# --- two user protocols, each argument conforming to exactly one ---
class Walker(Protocol):
    def walk(self) -> int: ...


class Swimmer(Protocol):
    def swim(self) -> int: ...


class Horse:
    def __init__(self, legs: int) -> None:
        self.legs = legs

    @readonly
    def walk(self) -> int:
        return self.legs


class Fish:
    def __init__(self, fins: int) -> None:
        self.fins = fins

    @readonly
    def swim(self) -> int:
        return self.fins


@dispatch
def move(x: Walker) -> str:
    return "walks on " + str(x.walk())


@dispatch
def move(x: Swimmer) -> str:
    return "swims with " + str(x.swim())


def two_protocols_section() -> None:
    h = Horse(4)
    f = Fish(2)
    print("two protocols:", move(h))  # tpyc: ok -- Horse conforms to Walker only
    print("two protocols:", move(f))  # tpyc: ok -- Fish conforms to Swimmer only
    h.legs = 3
    f.fins = 5
    print("two protocols:", move(h), move(f))


# --- explicit vs structural protocol: `list` declares `Iterable`, and only
# structurally has `__len__`; sema ranks the declared conformance first
# (declared first here; the other order is in the _order twin) ---
class HasLen(Protocol):
    def __len__(self) -> int: ...


@dispatch
def count(v: Iterable[int]) -> str:
    n = 0
    for _ in v:
        n += 1
    return "iterable " + str(n)


@dispatch
def count(v: HasLen) -> str:
    return "sized " + str(len(v))


def protocol_section() -> None:
    xs = [1, 2]
    print("protocol:", count(xs))  # the declared `Iterable` variant
    xs.append(3)
    print("protocol:", count(xs), xs)


def main() -> None:
    concrete_section()
    protocol_only_section()
    two_protocols_section()
    protocol_section()


main()
