# All match arms return -- code after match is unreachable but allowed
class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def describe(a: Dog | Cat) -> str:
    match a:
        case Dog(name=n):
            return "dog: " + n
        case _:
            return "other"

def main() -> None:
    d: Dog | Cat = Dog("Rex")
    c: Dog | Cat = Cat("Whiskers")
    print(describe(d))
    print(describe(c))

main()
