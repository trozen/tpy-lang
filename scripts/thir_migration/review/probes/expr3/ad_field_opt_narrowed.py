from typing import Protocol
from tpy import dynamic, Int32
@dynamic
class Speaker(Protocol):
    def speak(self) -> Int32: ...
class Dog:
    def __init__(self) -> None:
        pass
    def speak(self) -> Int32:
        return 1
def talk(s: Speaker | None) -> Int32:
    if s is None:
        return 0
    return s.speak()

class H:
    dog: Dog | None
    def __init__(self) -> None:
        self.dog = Dog()
def main() -> None:
    h = H()
    d = h.dog
    if d is not None:
        print(talk(d))
main()
