# A protocol method's default names a constant as a plain function's does:
# a module variable that can be rebound is no default (the restriction in
# docs/LANGUAGE_FEATURES.md, "Default parameter values").
from typing import Protocol
from tpy import int32

STEP: int32 = 3


class Stepper(Protocol):
    def step(self, by: int32 = STEP) -> int32: ...  # tpyc: error(/Default parameter value 'STEP' must be a module-level Final\[T\] constant/)


class Counter:
    def __init__(self) -> None:
        pass

    def step(self, by: int32 = 1) -> int32:
        return by


def advance(s: Stepper) -> int32:
    return s.step()


def main() -> None:
    print(advance(Counter()))


main()
