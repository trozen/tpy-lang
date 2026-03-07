# error: class pattern for a type that is not a member of the union
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

def describe(a: Dog | Cat) -> str:
    match a:
        case Bird():  # tpyc: error(/not a member/)
            return "bird"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()
