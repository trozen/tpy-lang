# match/case with guard clauses (if conditions)
from dataclasses import dataclass

@dataclass
class Dog:
    name: str

@dataclass
class Cat:
    name: str

def describe(a: Dog | Cat) -> str:
    match a:
        case Dog(name=n) if n == "Rex":
            return "Rex the dog!"
        case Dog():
            return "some dog"
        case Cat(name=n) if n == "Whiskers":
            return "Whiskers the cat!"
        case _:
            return "other"
    return ""

def main() -> None:
    d1: Dog | Cat = Dog("Rex")
    d2: Dog | Cat = Dog("Buddy")
    c1: Dog | Cat = Cat("Whiskers")
    c2: Dog | Cat = Cat("Luna")
    print(describe(d1))
    print(describe(d2))
    print(describe(c1))
    print(describe(c2))

main()
