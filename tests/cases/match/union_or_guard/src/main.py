# match/case or-patterns combined with guards on union subjects
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

def describe(a: Dog | Cat | Bird, verbose: bool) -> str:
    match a:
        case Dog() | Cat() if verbose:
            return "verbose pet"
        case Dog() | Cat():
            return "pet"
        case Bird():
            return "bird"
    return ""

def find(a: Dog | Cat | Bird) -> str:
    match a:
        case Dog(name=n) | Cat(name=n) if n == "Rex":
            return "found Rex"
        case Dog(name=n) | Cat(name=n):
            return "other pet: " + n
        case Bird(name=n):
            return "bird: " + n
    return ""

def main() -> None:
    d: Dog | Cat | Bird = Dog("Rex")
    c: Dog | Cat | Bird = Cat("Luna")
    b: Dog | Cat | Bird = Bird("Tweety")
    print(describe(d, True))
    print(describe(d, False))
    print(describe(c, True))
    print(describe(b, False))
    print(find(d))
    print(find(c))
    print(find(b))

main()
