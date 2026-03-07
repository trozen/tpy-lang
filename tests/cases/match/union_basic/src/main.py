# match/case on a 3-member union with class patterns and wildcard default
class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Bird:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def describe(a: Dog | Cat | Bird) -> str:
    match a:
        case Dog():
            return "dog"
        case Cat():
            return "cat"
        case _:
            return "other"

def main() -> None:
    d: Dog | Cat | Bird = Dog("Rex")
    c: Dog | Cat | Bird = Cat("Whiskers")
    b: Dog | Cat | Bird = Bird("Tweety")
    print(describe(d))
    print(describe(c))
    print(describe(b))

main()
