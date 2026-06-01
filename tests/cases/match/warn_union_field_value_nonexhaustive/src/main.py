# Conditional arms don't cover their variant: a sole `Dog(legs=4)` arm leaves
# both Dog and Cat uncovered, so exhaustiveness must still report Dog missing.
class Dog:
    legs: int
    def __init__(self, legs: int) -> None:
        self.legs = legs

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def describe(a: Dog | Cat) -> str:
    result = "none"
    match a:  # tpyc: warning(/non-exhaustive match on 'Cat \| Dog'; missing: Cat, Dog/)
        case Dog(legs=4):
            result = "quad dog"
    return result

def main() -> None:
    print(describe(Dog(4)))
    print(describe(Dog(2)))
    print(describe(Cat("x")))

main()
