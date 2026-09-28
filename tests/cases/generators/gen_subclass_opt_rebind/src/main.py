# A plain-class subclass rvalue rebind of an Optional[Base] frame local
# is ACCEPTED with the declared upcast-narrows warning: the site slots
# are base-typed, so the value narrows to Animal on write -- pinned
# together with the @dynamic form's reject (error_gen_dyn_opt_rebind).
from typing import Iterator, Optional


class Animal:
    kind: str

    def __init__(self) -> None:
        self.kind = "animal"

    def name(self) -> str:
        return self.kind


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()
        self.kind = "dog"


class Cat(Animal):
    def __init__(self) -> None:
        super().__init__()
        self.kind = "cat"


def gen() -> Iterator[str]:
    # Both writes narrow into base-typed site slots; `kind` set by each
    # subclass ctor survives the slice, so the reads stay CPython-equal.
    p: Optional[Animal] = Dog()  # tpyc: warning(/upcast narrows/)
    yield "start"
    p = Cat()
    if p is not None:
        yield p.name()


def main() -> None:
    for s in gen():
        print(s)


main()
