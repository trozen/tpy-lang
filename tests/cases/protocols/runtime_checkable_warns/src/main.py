# isinstance() against a protocol that is not @runtime_checkable warns: TPy
# answers it at compile time, where CPython raises TypeError.
from typing import Protocol, Sized, runtime_checkable
from tpy import Equatable, Hashable, Span, auto_readonly, int32


class Speaker(Protocol):
    def speak(self) -> str: ...


class Walker(Protocol):
    def walk(self) -> int32: ...


class Dog:
    def __init__(self) -> None:
        pass

    def speak(self) -> str:
        return "woof"


class Cat:
    def __init__(self) -> None:
        pass

    def walk(self) -> int32:
        return 4


# user: an undecorated user protocol
def user(x: Speaker | Walker) -> str:
    if isinstance(x, Speaker):  # tpyc: warning(/TPy-only/)
        return x.speak()
    return "walks"


# stub: Hashable stays undecorated, since every CPython object has __hash__
def stub(x: Sized) -> str:
    if isinstance(x, Hashable):  # tpyc: warning(/TPy-only/)
        return "hashable"
    return "unhashable"


# equatable: Equatable stays undecorated, since object provides __eq__
def equatable(x: Sized) -> str:
    if isinstance(x, Equatable):  # tpyc: warning(/TPy-only/)
        return "eq"
    return "noeq"


@runtime_checkable
class Eq(Protocol):
    def __eq__(self, other: "Eq") -> bool: ...


# eq_row: a class without __eq__ is no Eq in TPy, while CPython finds
# object.__eq__ (BUGS.md#isinstance-protocol-signatures-vs-names, row 5)
def eq_row(x: Eq | Walker) -> str:
    if isinstance(x, Eq):  # tpyc: ok
        return "eq yes"
    return "eq no"


class Checker:
    data: list[int32]

    def __init__(self) -> None:
        self.data = [7, 8]

    # twice: the const clone analyzes the body again; the warning shows once
    @auto_readonly
    def pick(self, x: Speaker | Walker) -> Span[auto_readonly[int32]]:
        if isinstance(x, Speaker):  # tpyc: warning(/TPy-only/)
            return self.data
        return self.data


def main() -> None:
    d = Dog()
    c = Cat()
    print("user", user(d), user(c))
    xs = [1, 2]
    print("stub", stub(xs))
    print("equatable", equatable(xs))
    print("eq_row", eq_row(c))
    k = Checker()
    s = k.pick(d)
    print("twice", s[0], s[1])


main()
