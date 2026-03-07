# Pattern binding can be reassigned inside the match arm body
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
            n = "dog:" + n
        case Cat(name=n):
            n = "cat:" + n
    return n

def main() -> None:
    d: Dog | Cat = Dog("Rex")
    c: Dog | Cat = Cat("Whiskers")
    print(describe(d))
    print(describe(c))

main()
