# error: flattening a nested or-pattern does not make it usable as a class
# field sub-pattern -- `P(a=1 | (2 | 3))` still gets the field-binding
# diagnostic the flat spelling gets.
from dataclasses import dataclass


@dataclass
class P:
    a: int


def pick(p: P) -> int:
    match p:
        case P(a=1 | (2 | 3)):  # tpyc: error(/Unsupported sub-pattern in field binding/)
            return 1
        case _:
            return 0
    return 0


def main() -> None:
    pass


main()
