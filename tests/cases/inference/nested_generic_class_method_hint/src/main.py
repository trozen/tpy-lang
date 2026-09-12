# Bidir-hint seed on the methods.py path where the receiver is a generic
# class AND the method introduces its own type params. Exercises the
# class_subst-applied `partial_func` interaction with `seed_subst_from_return_hint`:
# class T is pre-substituted before the seed runs, so the seed should only
# bind method-introduced U from the LHS hint.
from typing import Protocol
from tpy import dynamic, Own, int32
from tplib import Box, Rc


@dynamic
class Greeter(Protocol):
    def greet(self) -> str: ...


class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def greet(self) -> str:
        return self.name


class Container[T]:
    payload: T
    def __init__(self, val: Own[T]) -> None:
        self.payload = val
    # Method introduces its own type param U; the receiver's T (int32 below)
    # is irrelevant to U's binding. LHS hint Rc[Box[Greeter]] should seed
    # U=Box[Greeter] and propagate Box[Greeter] as the inner Box(Cat(...))
    # hint, so Box's record-construction LHS-hint preference flips its
    # internal T from Cat to Greeter.
    def wrap[U](self, value: Own[U]) -> Own[Rc[U]]:
        return Rc.new(value)


def main() -> None:
    c: Container[int32] = Container(0)
    r: Rc[Box[Greeter]] = c.wrap(Box(Cat("Whiskers")))  # tpyc: type(Rc[Box[Greeter]])
    print(r.get().get().greet())


main()
