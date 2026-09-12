from typing import overload, Literal
from tpy import int32
@overload
def h(a: Literal["x"]) -> int32: ...
@overload
def h(a: str, b: int32) -> int32: ...
def h(a: str, b: int32 = 0) -> int32:
    return b
def main() -> None:
    print(h("x"), h("y", 2))
main()
