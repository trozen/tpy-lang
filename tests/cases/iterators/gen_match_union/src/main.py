# H1: union-subject `match` in a generator method, with a field binding
# (`name=n`) and an `as` binding (`as c`) both read across a yield -- the
# pattern-bound names are frame fields, so they survive the state split.
# (Subject is a `self` field, not a union param, to avoid the unrelated
#  union-generator-param call-site bug tracked in BUGS.md.)
from typing import Iterator


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Cat:
    lives: int

    def __init__(self, lives: int) -> None:
        self.lives = lives


class Box:
    payload: Dog | Cat

    def __init__(self, p: Dog | Cat) -> None:
        self.payload = p

    def describe(self) -> Iterator[str]:
        match self.payload:
            case Dog(name=n):
                yield "dog"
                yield n
            case Cat() as c:
                yield "cat"
                yield str(c.lives)


def main() -> None:
    b = Box(Dog("rex"))
    for s in b.describe():
        print(s)
    print("--")
    b2 = Box(Cat(9))
    for s in b2.describe():
        print(s)


main()
