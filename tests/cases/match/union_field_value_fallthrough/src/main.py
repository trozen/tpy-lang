# Conditional (`Dog(legs=4)`) and bare-type (`Dog()`) arms on the same variant
# are distinct: a non-matching value falls through. Covers int and str literal
# fields, multiple literals on one variant, and a literal in an or-alternative.
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
            return "quad dog"
        case Dog(legs=3):  # tpyc: ok
            return "tripod dog"
        case Dog():
            return "some dog"
        case Cat(name="rex"):  # tpyc: ok
            return "rex cat"
        case Cat():
            return "cat"

def either(a: Dog | Cat) -> str:
    match a:
        case Dog(legs=4) | Cat():  # tpyc: ok
            return "quaddog-or-cat"
        case _:
            return "other"

def main() -> None:
    print(describe(Dog(4)))
    print(describe(Dog(3)))
    print(describe(Dog(2)))
    print(describe(Cat("rex")))
    print(describe(Cat("x")))
    print(either(Dog(4)))
    print(either(Dog(2)))
    print(either(Cat("x")))

main()
