# error: duplicate class pattern for the same union member
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
        case Dog():
            return "dog"
        case Dog():  # tpyc: error(/duplicate case/)
            return "dog again"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()
