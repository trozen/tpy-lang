# @dynamic protocol types in method and constructor parameters
from tpy import dynamic
from typing import Protocol

@dynamic
class Speaker(Protocol):
    def speak(self) -> str:
        ...

class Dog(Speaker):
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def speak(self) -> str:
        return "Woof from " + self.name

class Cat(Speaker):
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def speak(self) -> str:
        return "Meow from " + self.name

class Recorder:
    message: str

    def __init__(self, s: Speaker) -> None:
        self.message = s.speak()

class Announcer:
    prefix: str

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def announce(self, s: Speaker) -> None:
        print(self.prefix + s.speak())

def main() -> None:
    d = Dog("Rex")
    c = Cat("Whiskers")
    r = Recorder(d)
    print(r.message)
    ann = Announcer(">> ")
    ann.announce(d)
    ann.announce(c)

main()
