from tpy import Int32, dynamic, Own
from typing import Protocol
@dynamic
class Speaker(Protocol):
    def speak(self) -> Int32: ...
class Dog:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
    def speak(self) -> Int32:
        return self.n
def a(xs: list[Own[Speaker]]) -> Speaker:
    return xs[0]
def main() -> None:
    print(1)
main()
