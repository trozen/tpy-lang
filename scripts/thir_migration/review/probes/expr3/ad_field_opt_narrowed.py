from typing import Protocol
from tpy import dynamic, int32
@dynamic
class Speaker(Protocol):
    def speak(self) -> int32: ...
class Dog:
    def __init__(self) -> None:
        pass
    def speak(self) -> int32:
        return 1
def talk(s: Speaker | None) -> int32:
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
