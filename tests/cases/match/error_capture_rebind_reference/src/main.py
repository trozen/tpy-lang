# A reference-typed match capture rebound in its arm is rejected. The binding
# aliases the matched object (`auto&`); CPython's semantics are an alias up to
# the rebind, then a re-seated local pointing elsewhere -- not yet modeled, and
# neither a copy (silent divergence: the pre-rebind mutation-through would be
# lost) nor the alias (write-through corrupts the subject) matches. Reject
# rather than silently diverge.
class Pet:
    name: str
    def __init__(self, n: str) -> None:
        self.name = n


class Cat:
    pet: Pet
    def __init__(self, p: Pet) -> None:
        self.pet = p


def rebind_assign(a: Cat, other: Pet) -> None:
    match a:
        case Cat(pet=p):  # tpyc: error(/capture 'p' aliases the matched object.*is rebound/)
            p.name = "x"  # CPython: mutates a.pet through the alias ...
            p = other     # ... then re-seats p to another object
            p.name = "y"


def main():
    rebind_assign(Cat(Pet("q")), Pet("z"))


main()
