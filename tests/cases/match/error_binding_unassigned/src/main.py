# Error: pattern binding used after match but not defined in all arms
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
        case _:
            pass
    return n  # tpyc: error(/may not be assigned at this point/)

def main() -> None:
    d: Dog | Cat = Dog("Rex")
    print(describe(d))

main()
