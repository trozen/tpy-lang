# error: the same-bindings check for or-patterns sees through a flattened
# group -- `B()` binds no `p`, so the arm is still rejected.
from dataclasses import dataclass


@dataclass
class A:
    v: int


@dataclass
class B:
    w: int


@dataclass
class C:
    v: int


def pick(x: A | B | C) -> int:
    match x:
        case (A(v=p) | B()) | C(v=p):  # tpyc: error(/not bound in all alternatives/)
            return p
        case _:
            return 0
    return 0


def main() -> None:
    pass


main()
