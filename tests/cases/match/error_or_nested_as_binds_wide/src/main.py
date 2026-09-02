# error: an `as` inside an or-pattern is a binding boundary, so the group it
# wraps is NOT merged into the outer alternative list -- `y` keeps binding the
# whole group (`Dog | Cat | Bird`) rather than one member.
# The rejection itself is a sema limitation, not a rule: see
# BUGS.md#or-pattern-as-binding-no-join.
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


def pick(x: Dog | Cat | Bird) -> int:
    match x:
        case ((Dog() | Cat()) as y) | (Bird() as y):  # tpyc: error(/variable 'y' has type/)
            return 1
        case _:
            return 0
    return 0


def main() -> None:
    pass


main()
