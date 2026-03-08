# error: or-pattern alternatives must bind the same variable names
from dataclasses import dataclass

@dataclass
class Dog:
    name: str

@dataclass
class Cat:
    age: int

def describe(a: Dog | Cat) -> str:
    match a:
        case Dog(name=n) | Cat(age=a):  # tpyc: error(/not bound in all alternatives/)
            return "pet"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()
