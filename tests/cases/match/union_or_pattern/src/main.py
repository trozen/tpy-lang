# match/case or-patterns on union subjects (no bindings)
from dataclasses import dataclass

@dataclass
class Dog:
    name: str

@dataclass
class Cat:
    name: str

@dataclass
class Bird:
    name: str

def describe(a: Dog | Cat | Bird) -> str:
    match a:
        case Dog() | Cat():
            return "mammal"
        case Bird():
            return "bird"
    return ""

def main() -> None:
    d: Dog | Cat | Bird = Dog("Rex")
    c: Dog | Cat | Bird = Cat("Whiskers")
    b: Dog | Cat | Bird = Bird("Tweety")
    print(describe(d))
    print(describe(c))
    print(describe(b))

main()
