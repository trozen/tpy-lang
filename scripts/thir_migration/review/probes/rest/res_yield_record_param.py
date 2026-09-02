from tpy import Int32
class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
from typing import Iterator
def rep(b: P) -> Iterator[P]:
    yield b
    yield b
def main() -> None:
    pass
main()
