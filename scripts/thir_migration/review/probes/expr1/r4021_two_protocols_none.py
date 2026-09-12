from typing import Protocol
from tpy import int32
class Shape(Protocol):
    def area(self) -> int32: ...
class Named(Protocol):
    def name(self) -> str: ...
def f(v: Shape | Named | None) -> bool:
    return v is None
def main() -> None:
    pass
main()
