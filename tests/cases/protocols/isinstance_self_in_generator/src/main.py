# isinstance(self, Sub) inside a generator method body. Generators share the
# resumable frame with `async def` (which already supports this), so the
# narrowed self works: `polymorphic_cast_arg` emits `&__self` as the
# dynamic_cast input. Covers the basic narrowing, a @readonly generator
# method (const __self -> const Sub*), and mutation through the narrowed self
# across a yield. (A subclass-only field access *after* a suspension inside the
# narrowed block is covered by isinstance_self_in_generator_field.)
from typing import Protocol, Iterator
from tpy import dynamic, readonly


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    _name: str
    _n: int

    def __init__(self, nm: str) -> None:
        self._name = nm
        self._n = 0

    @readonly
    def ro_names(self) -> Iterator[str]:
        if isinstance(self, Dog):  # tpyc: ok
            yield "ro-dog:" + self._name
        else:
            yield "ro-pet:" + self._name

    def counts(self) -> Iterator[int]:
        if isinstance(self, Dog):  # tpyc: ok
            self._n += 1
            yield self._n
            self._n += 10
            yield self._n
        else:
            yield -1


class Dog(Pet):
    def __init__(self, nm: str) -> None:
        super().__init__(nm)


def main() -> None:
    p = Pet("rex")
    d = Dog("fido")
    for s in p.ro_names():
        print(s)
    for s in d.ro_names():
        print(s)
    for v in p.counts():
        print(v)
    for v in d.counts():
        print(v)


main()
