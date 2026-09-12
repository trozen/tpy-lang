from typing import Protocol
from tpy import int32
class Speaker(Protocol):
    def speak(self) -> int32: ...
class Dog:
    def speak(self) -> int32:
        return 1
def use(x: Speaker | None) -> int32:
    if x is not None:
        r = x.speak()
    else:
        r = 0
    return r
def main() -> None:
    print(use(Dog()))
main()
