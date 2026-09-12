from tpy import int32, dynamic, Own
from typing import Protocol
@dynamic
class Speaker(Protocol):
    def speak(self) -> int32: ...
class Dog:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def speak(self) -> int32:
        return self.n
def a(xs: list[Own[Speaker]]) -> Own[Speaker]:
    return xs[0]
def main() -> None:
    print(1)
main()
