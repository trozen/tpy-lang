# readonly[Optional[@dynamic protocol]] at a parameter position is allowed:
# readonly is a transparent same-position wrapper, so it lowers to a
# `const Pet*` borrow like a bare Optional[Pet] param. is-None narrowing and a
# @readonly protocol method dispatch through the const pointer.
from typing import Protocol, Optional
from tpy import dynamic, readonly


@dynamic
class Pet(Protocol):
    @readonly
    def name(self) -> str: ...


class Dog(Pet):
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    @readonly
    def name(self) -> str:
        return self.tag


def greet(p: readonly[Optional[Pet]]) -> str:
    if p is None:                 # tpyc: ok
        return "<none>"
    return p.name()


def main() -> None:
    print(greet(Dog("rex")))
    print(greet(None))


main()
