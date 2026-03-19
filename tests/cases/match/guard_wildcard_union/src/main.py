# match/case with wildcard guard and as-pattern on union subjects
from dataclasses import dataclass

@dataclass
class Dog:
    name: str

@dataclass
class Cat:
    name: str

def classify(a: Dog | Cat, strict: bool) -> str:
    match a:
        case Dog(name=n) if n == "Rex":
            return "Rex!"
        case _ if strict:
            return "strict other"
        case _:
            return "other"
    return ""

def as_guard(a: Dog | Cat) -> str:
    match a:
        case Dog(name=n) as d if n == "Buddy":
            return d.name + " the dog"
        case Dog(name=n):
            return "dog: " + n
        case Cat(name=n):
            return "cat: " + n
    return ""

def main() -> None:
    d1: Dog | Cat = Dog("Rex")
    d2: Dog | Cat = Dog("Buddy")
    c: Dog | Cat = Cat("Luna")
    print(classify(d1, True))
    print(classify(d2, True))
    print(classify(d2, False))
    print(classify(c, True))
    print(classify(c, False))
    print(as_guard(d1))
    print(as_guard(d2))
    print(as_guard(c))

main()
