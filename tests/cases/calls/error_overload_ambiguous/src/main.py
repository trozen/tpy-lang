# Two overloads whose protocol params are distinct but cover overlapping
# structural surfaces: a type implementing both conforms to each at the same
# specificity tier and widening cost, so there is no principled winner.
# The compiler must refuse to pick arbitrarily (the pre-refactor bug was to
# silently pick the first-declared overload via stable-sort tiebreak).
from typing import Protocol
from tpy import dispatch


class Greeter(Protocol):
    def greet(self) -> str: ...


class Farewell(Protocol):
    def greet(self) -> str: ...


class Hybrid:
    def __init__(self) -> None:
        pass

    def greet(self) -> str:
        return "hi"


@dispatch
def describe(x: Greeter) -> str:
    return "greeter: " + x.greet()


@dispatch
def describe(x: Farewell) -> str:
    return "farewell: " + x.greet()


def main() -> None:
    h = Hybrid()
    print(describe(h))  # tpyc: error(/ambiguous|matching/i)


main()
