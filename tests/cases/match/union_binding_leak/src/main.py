# Pattern binding leaks into enclosing scope when all arms define it
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
            pass
        case Cat(name=n):
            pass
    return n

def main() -> None:
    d: Dog | Cat = Dog("Rex")
    c: Dog | Cat = Cat("Whiskers")
    print(describe(d))
    print(describe(c))

main()
