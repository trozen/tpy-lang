# warning: non-exhaustive match on union (missing member)
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
    match a:  # tpyc: warning(/non-exhaustive match.*missing: Bird.*case _:/)
        case Dog():
            return "dog"
        case Cat():
            return "cat"
    return "unknown"

def main() -> None:
    d: Dog | Cat | Bird = Dog("Rex")
    print(describe(d))

main()
