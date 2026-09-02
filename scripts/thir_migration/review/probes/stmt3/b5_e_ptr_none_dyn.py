from tpy import Int32, dynamic, Own, Ptr
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
def main() -> None:
    p: Ptr[Speaker] = None
    print(1 if p is None else 0)
main()
