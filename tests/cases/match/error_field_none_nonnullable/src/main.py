# `field=None` on a non-nullable field can never match -- rejected with a
# guard hint rather than silently matching every instance.
class Dog:
    legs: int
    def __init__(self, legs: int) -> None:
        self.legs = legs

def describe(d: Dog) -> str:
    match d:
        case Dog(legs=None):  # tpyc: error(/field 'legs' of type 'int' cannot be None/)
            return "x"
        case _:
            return "y"
