from typing import overload, Literal
from tpy import int32
@overload
def norm(m: Literal["r", "w"]) -> int32: ...
@overload
def norm(m: Literal["x", "y"]) -> int32: ...
def norm(m: str) -> int32:
    m = "z"
    if m == "z":
        return 1
    return 2
def main() -> None:
    print(norm("r"), norm("x"))
main()
