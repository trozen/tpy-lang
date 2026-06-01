# Two identical literal field arms on the same union variant are duplicates
# (the field-value constraint is folded into the dedup key).
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
        case Dog(legs=4):
            return "quad"
        case Dog(legs=4):  # tpyc: error(/duplicate case for 'Dog'/)
            return "dup"
        case _:
            return "other"
