# error: or-pattern alternatives bind same name but different types
from dataclasses import dataclass

@dataclass
class Dog:
    name: str

@dataclass
class Cat:
    age: int

def describe(a: Dog | Cat) -> str:
    match a:
        case Dog(name=n) | Cat(age=n):  # tpyc: error(/variable 'n' has type/)
            return str(n)
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()
