# Narrowing a non-value union param via isinstance inside a generator body
# then accessing an arm-specific member. The narrowed binding must be
# established on the captured frame field (regression: codegen emitted the
# member access on the raw std::variant). Covers both a narrow that follows
# a suspension and one with no preceding yield.
from typing import Iterator


class Dog:
    def sound(self) -> str:
        return "woof"


class Cat:
    def sound(self) -> str:
        return "meow"


class Owner:
    def voices(self, a: Dog | Cat) -> Iterator[str]:
        yield "start"
        if isinstance(a, Dog):  # tpyc: ok
            yield a.sound()
        else:
            yield "not-a-dog"

    def first(self, a: Dog | Cat) -> Iterator[str]:
        if isinstance(a, Dog):  # tpyc: ok
            yield a.sound()
        else:
            yield a.sound()


def main() -> None:
    o = Owner()
    for s in o.voices(Dog()):
        print(s)
    for s in o.voices(Cat()):
        print(s)
    for s in o.first(Cat()):
        print(s)


main()
