# A guard on an arm whose pattern is a parenthesized or-pattern group: the
# guard belongs to the arm, so flattening the group must leave it applying to
# every alternative.
from dataclasses import dataclass


@dataclass
class Dog:
    n: int


@dataclass
class Cat:
    n: int


@dataclass
class Bird:
    n: int


def classify(a: Dog | Cat | Bird, allow: bool) -> str:
    match a:
        # The guard gates all three alternatives, not just the last one.
        case (Dog() | Cat()) | Bird() if allow:
            return "allowed"
        case _:
            return "blocked"


def small(n: int, allow: bool) -> str:
    match n:
        case (1 | 2) | 3 if allow:
            return "small-allowed"
        case 1 | 2 | 3:
            return "small-blocked"
        case _:
            return "other"


def main() -> None:
    d: Dog | Cat | Bird = Dog(1)
    c: Dog | Cat | Bird = Cat(2)
    b: Dog | Cat | Bird = Bird(3)
    print(classify(d, True))
    print(classify(d, False))
    print(classify(c, True))
    print(classify(c, False))
    print(classify(b, True))
    print(classify(b, False))
    print(small(1, True))
    print(small(2, False))
    print(small(3, True))
    print(small(9, True))


main()
