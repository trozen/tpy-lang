# @readonly function with match/case guard on union param
from tpy import readonly

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

@readonly
def describe(pet: Dog | Cat) -> str:
    match pet:
        case Dog(name=n) if len(n) > 3:
            return "long-named dog: " + n
        case Dog(name=n):
            return "dog: " + n
        case Cat(name=n):
            return "cat: " + n

def main() -> None:
    print(describe(Dog("Buddy")))
    print(describe(Dog("Rex")))
    print(describe(Cat("Whiskers")))

main()
