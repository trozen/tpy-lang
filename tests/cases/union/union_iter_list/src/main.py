# Iterating over list of non-value union (value-variant elements)

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def main() -> None:
    pets: list[Dog | Cat] = [Dog("Rex"), Cat("Whiskers"), Dog("Buddy")]
    for p in pets:
        if isinstance(p, Dog):
            print(p.name)
        if isinstance(p, Cat):
            print(p.name)

main()
