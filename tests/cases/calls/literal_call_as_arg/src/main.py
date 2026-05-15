# Passing a `Literal[str]`-returning call directly as an argument to a
# `Literal`-overloaded function dispatches to the Literal specialization
# without an intermediate local.
from typing import Literal, overload
from tpy import Int32


def get_r() -> Literal["r"]:
    return "r"


@overload
def pick(v: Literal["r", "w"]) -> Int32: ...
@overload
def pick(v: str) -> Int32: ...
def pick(v: str) -> Int32:
    return Int32(99)


def main() -> None:
    print(pick(get_r()))


main()
