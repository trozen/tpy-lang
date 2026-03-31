# Literal subset mismatch: multi-value Literal arg doesn't match
# a narrower Literal param when not all values are in the param's set
from typing import Literal, overload


@overload
def f(x: Literal["r"]) -> None: ...

@overload
def f(x: Literal["rb"]) -> None: ...

def f(x: str) -> None:
    pass


def g(mode: Literal["r", "rb"]) -> None:
    f(mode)  # tpyc: error(/No matching/)
