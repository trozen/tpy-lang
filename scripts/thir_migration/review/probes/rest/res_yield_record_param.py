from tpy import int32
class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
from typing import Iterator
def rep(b: P) -> Iterator[P]:
    yield b
    yield b
def main() -> None:
    pass
main()
