# match-arm subject narrowing re-established across a suspension in a generator:
# the arm's narrowed subject (and a pattern capture) must survive a yield and
# read the matched type afterwards. Covers a class-pattern arm, an `as` capture,
# a guard on a separate variable, and a reassign-in-arm that kills the narrowing.
from typing import Iterator


class Dog:
    def sound(self) -> str:
        return "woof"


class Cat:
    def sound(self) -> str:
        return "meow"


def voices(a: Dog | Cat) -> Iterator[str]:
    match a:
        case Dog():
            yield "is-dog"
            yield a.sound()
        case Cat():
            yield a.sound()


def capture(a: Dog | Cat) -> Iterator[str]:
    match a:
        case Dog() as d:
            yield "got-dog"
            yield d.sound()
        case Cat():
            yield a.sound()


def guarded(a: int | str, allow: bool) -> Iterator[str]:
    match a:
        case int() if allow:
            yield "big"
            yield str(a + 1)
        case _:
            yield "other"


def remake() -> int | str:
    return "z"


def kill(a: int | str) -> Iterator[str]:
    match a:
        case int():
            yield "int:" + str(a + 1)
            a = remake()
            yield "rebound"
            match a:
                case str():
                    yield "str:" + a
                case _:
                    yield "still-int"
        case _:
            yield "not-int"


def main() -> None:
    for s in voices(Dog()):
        print(s)
    for s in voices(Cat()):
        print(s)
    for s in capture(Dog()):
        print(s)
    for s in guarded(42, True):
        print(s)
    for s in guarded(3, False):
        print(s)
    for s in kill(5):
        print(s)


main()
