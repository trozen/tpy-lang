from typing import Protocol
from tpy import Int32
class Speaker(Protocol):
    def speak(self) -> Int32: ...
class Dog:
    def speak(self) -> Int32:
        return 1
def use(x: Speaker | None) -> Int32:
    if x is not None:
        r = x.speak()
    else:
        r = 0
    return r
def main() -> None:
    print(use(Dog()))
main()
