# Two overloads whose protocol params are distinct but cover overlapping
# structural surfaces: a type implementing both conforms to each at the same
# specificity tier and widening cost, so there is no principled winner.
# The compiler must refuse to pick arbitrarily (the pre-refactor bug was to
# silently pick the first-declared overload via stable-sort tiebreak).
from typing import Protocol, overload


class Greeter(Protocol):
    def greet(self) -> str: ...


class Farewell(Protocol):
    def greet(self) -> str: ...


class Hybrid:
    def __init__(self) -> None:
        pass

    def greet(self) -> str:
        return "hi"


@overload
def describe(x: Greeter) -> str:  # tpyc: ok
    return "greeter: " + x.greet()


@overload
def describe(x: Farewell) -> str:  # tpyc: ok
    return "farewell: " + x.greet()


def main() -> None:
    h = Hybrid()
    print(describe(h))  # tpyc: error(/ambiguous|matching/i)


main()
