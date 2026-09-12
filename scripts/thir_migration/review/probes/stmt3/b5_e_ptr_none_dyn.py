from tpy import int32, dynamic, Own, Ptr
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
def main() -> None:
    p: Ptr[Speaker] = None
    print(1 if p is None else 0)
main()
