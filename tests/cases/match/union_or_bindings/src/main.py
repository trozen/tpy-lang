# match/case or-patterns on unions with variable bindings
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
        case Dog(name=n) | Cat(name=n):
            return "pet: " + n
        case Bird(name=n):
            return "bird: " + n
    return ""

def main() -> None:
    d: Dog | Cat | Bird = Dog("Rex")
    c: Dog | Cat | Bird = Cat("Whiskers")
    b: Dog | Cat | Bird = Bird("Tweety")
    print(describe(d))
    print(describe(c))
    print(describe(b))

main()
