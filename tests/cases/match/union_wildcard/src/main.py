# match/case with wildcard and capture patterns
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

def classify(a: Dog | Cat) -> str:
    match a:
        case Dog():
            return "is dog"
        case x:
            return "not dog"

def main() -> None:
    d: Dog | Cat = Dog("Rex")
    c: Dog | Cat = Cat("Whiskers")
    print(describe(d))
    print(describe(c))
    print(classify(d))
    print(classify(c))

main()
