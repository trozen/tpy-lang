from typing import overload, Literal
from tpy import Int32
@overload
def norm(m: Literal["r", "w"]) -> Int32: ...
@overload
def norm(m: Literal["x", "y"]) -> Int32: ...
def norm(m: str) -> Int32:
    m = "z"
    if m == "z":
        return 1
    return 2
def main() -> None:
    print(norm("r"), norm("x"))
main()
