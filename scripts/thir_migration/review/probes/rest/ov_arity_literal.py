from typing import overload, Literal
from tpy import Int32
@overload
def h(a: Literal["x"]) -> Int32: ...
@overload
def h(a: str, b: Int32) -> Int32: ...
def h(a: str, b: Int32 = 0) -> Int32:
    return b
def main() -> None:
    print(h("x"), h("y", 2))
main()
