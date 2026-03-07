# error: guard clauses are not yet supported
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
        case Dog(name=n) if n == "Rex":  # tpyc: error(/guard.*not yet supported/)
            return "Rex!"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()
