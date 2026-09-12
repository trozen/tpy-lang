# Posonly enforcement reaches protocol-typed receivers: a positional-only
# protocol method param cannot be passed by keyword.
from typing import Protocol
from tpy import int32


class Scaler(Protocol):
    def scale(self, a: int32, /, k: int32) -> int32: ...


class Doubler:
    def scale(self, a: int32, /, k: int32) -> int32:
        return a * k


def use(s: Scaler) -> int32:
    return s.scale(a=2, k=3)  # tpyc: error(/parameter 'a' is positional-only/)


def main() -> None:
    print(use(Doubler()))


main()
