# Passing a `Literal[str]`-returning call directly as an argument to a
# `Literal`-overloaded function dispatches to the Literal specialization
# without an intermediate local.
from typing import Literal, overload
from tpy import int32


def get_r() -> Literal["r"]:
    return "r"


@overload
def pick(v: Literal["r", "w"]) -> int32: ...
@overload
def pick(v: str) -> int32: ...
def pick(v: str) -> int32:
    return int32(99)


def main() -> None:
    print(pick(get_r()))


main()
