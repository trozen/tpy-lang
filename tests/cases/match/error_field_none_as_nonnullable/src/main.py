# The `field=(None as n)` as-pattern form is rejected on a non-nullable field
# too, not just the bare `field=None` form.
class Dog:
    legs: int
    def __init__(self, legs: int) -> None:
        self.legs = legs

def describe(d: Dog) -> str:
    match d:
        case Dog(legs=(None as n)):  # tpyc: error(/field 'legs' of type 'int' cannot be None/)
            return "x"
        case _:
            return "y"
