# Test elif isinstance chain for 3-way union narrowing
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
    if isinstance(a, Dog):
        return "dog: " + a.name
    elif isinstance(a, Cat):
        return "cat: " + a.name
    elif isinstance(a, Bird):
        return "bird: " + a.name
    return "unknown"

def main() -> None:
    d: Dog | Cat | Bird = Dog("Rex")
    c: Dog | Cat | Bird = Cat("Whiskers")
    b: Dog | Cat | Bird = Bird("Tweety")
    print(describe(d))
    print(describe(c))
    print(describe(b))

main()
