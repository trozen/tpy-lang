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
def f(p: Speaker | None) -> int32:
    if p is not None:
        return p.speak()
    return 0
def main() -> None:
    print(f(None))
main()
