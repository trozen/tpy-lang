# Three levels of parenthesized or-pattern groups: flattening is bottom-up,
# so arbitrary nesting depth collapses into one flat alternative list.
from dataclasses import dataclass


@dataclass
class A:
    n: int


@dataclass
class B:
    n: int


@dataclass
class C:
    n: int


@dataclass
class D:
    n: int


def pick(x: A | B | C | D) -> str:
    match x:
        # Equivalent to `case A() | B() | C() | D():` at every depth.
        case ((A() | B()) | C()) | D():
            return "any"
    return "none"


def value(x: A | B | C | D) -> int:
    match x:
        case ((A(n=v) | B(n=v)) | C(n=v)) | D(n=v):
            return v
    return -1


def main() -> None:
    a: A | B | C | D = A(1)
    b: A | B | C | D = B(2)
    c: A | B | C | D = C(3)
    d: A | B | C | D = D(4)
    print(pick(a), value(a))
    print(pick(b), value(b))
    print(pick(c), value(c))
    print(pick(d), value(d))


main()
