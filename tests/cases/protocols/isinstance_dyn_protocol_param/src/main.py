# isinstance(p, Sub) on a bare @dynamic protocol param now dispatches at
# runtime via dynamic_cast instead of folding statically. A protocol borrow
# only ever carries an inheritance conformer, so the cast is sound; the true
# branch narrows to the subclass (subclass-only method), the else branch keeps
# virtual protocol dispatch.
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def name(self) -> str:
        return self.tag

    def bark(self) -> str:
        return "woof " + self.tag


class Fish(Pet):
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def name(self) -> str:
        return self.label


def describe(p: Pet) -> str:
    if isinstance(p, Dog):       # tpyc: ok
        return p.bark()          # subclass-only method, narrowed
    return "pet " + p.name()     # virtual protocol dispatch


def main() -> None:
    print(describe(Dog("rex")))
    print(describe(Fish("nemo")))


main()
