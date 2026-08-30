# error: the duplicate-case check sees through a flattened group -- `A()`
# repeated across the group boundary is still a duplicate.
from dataclasses import dataclass


@dataclass
class A:
    v: int


@dataclass
class B:
    v: int


def pick(x: A | B) -> int:
    match x:
        case A() | (A() | B()):  # tpyc: error(/duplicate case/)
            return 1
        case _:
            return 0
    return 0


def main() -> None:
    pass


main()
