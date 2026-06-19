# Same static-true exhaustive-elif shape as isinstance_elif_exhaustive, but the
# branch bodies `raise` instead of `return` -- pins the TpyRaise arm of the
# post-narrowing static-true guard (the dead implicit-else must not emit a
# wrong-type extraction of the member the outer `if` already excluded).
class Dog:
    def __init__(self, n: int):
        self.n = n
class Cat:
    def __init__(self, n: int):
        self.n = n

class PetError(Exception):
    def __init__(self, code: int):
        self.code = code

def check(pet: Dog | Cat) -> None:
    if isinstance(pet, Dog):
        raise PetError(pet.n + 100)
    elif isinstance(pet, Cat):
        raise PetError(pet.n + 200)

def main() -> None:
    try:
        check(Cat(2))
    except PetError as e:
        print(e.code)
    try:
        check(Dog(1))
    except PetError as e:
        print(e.code)

main()
