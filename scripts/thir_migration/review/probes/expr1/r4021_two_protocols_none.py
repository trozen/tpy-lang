from typing import Protocol
from tpy import Int32
class Shape(Protocol):
    def area(self) -> Int32: ...
class Named(Protocol):
    def name(self) -> str: ...
def f(v: Shape | Named | None) -> bool:
    return v is None
def main() -> None:
    pass
main()
