# A literal field sub-pattern on a union arm (`Dog(legs=4)`) must compare the
# field, not match every Dog. Regression: the condition used to be dropped.
class Dog:
    legs: int
    def __init__(self, legs: int) -> None:
        self.legs = legs

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def describe(a: Dog | Cat) -> str:
    match a:
        case Dog(legs=4):  # tpyc: ok
            return "four-legged dog"
        case _:
            return "other"

def main() -> None:
    print(describe(Dog(3)))
    print(describe(Dog(4)))
    print(describe(Cat("x")))

main()
