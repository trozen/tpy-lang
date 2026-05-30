# A dict literal whose values are a covariant-generic wrapper upcast to the
# annotated value type: Box[Dog]/Box[Cat] -> Box[Pet]. The value-unification
# routes covariant targets through the per-value check against the annotation
# (like the union/optional value branch), so the dict builds at dict[str,
# Box[Pet]] directly instead of peer-unifying the values. Value-record slicing
# stays rejected (see dict/error_* / the variable-type mismatch).
from typing import Protocol
from tpy import dynamic
from tplib.box import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


class Cat(Pet):
    def name(self) -> str:
        return "cat"


def main() -> None:
    pets: dict[str, Box[Pet]] = {"a": Box(Dog()), "b": Box(Cat())}
    print(pets["a"].get().name())
    print(pets["b"].get().name())


main()
