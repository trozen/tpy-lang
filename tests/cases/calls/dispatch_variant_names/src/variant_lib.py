# Free overload sets defined in one module and called from another.
from typing import Literal, overload
from tpy import dispatch


class Box:
    def __init__(self, w: int) -> None:
        self.w = w


@dispatch
def grow(b: Box) -> str:
    b.w += 1
    return "box"


@dispatch
def grow(xs: list[int]) -> str:
    xs.append(0)
    return "list"


@overload
def mode_bits(m: Literal["r"]) -> int: ...
@overload
def mode_bits(m: Literal["w"]) -> int: ...
def mode_bits(m: str) -> int:
    if m == "r":
        return 4
    return 2
