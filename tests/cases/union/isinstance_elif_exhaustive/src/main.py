# A 2-member union exhausted by an if/elif: the elif's isinstance folds to a
# static-true branch, so its dead implicit-else must NOT emit a (wrong-type)
# extraction of the member the outer `if` already excluded.
class Dog:
    def __init__(self, n: int):
        self.n = n
    def sound(self) -> int:
        return self.n + 100
class Cat:
    def __init__(self, n: int):
        self.n = n
    def sound(self) -> int:
        return self.n + 200

def by_field(pet: Dog | Cat) -> int:
    if isinstance(pet, Dog):
        return pet.n
    elif isinstance(pet, Cat):
        return pet.n
    return -1

def by_method(pet: Dog | Cat) -> int:
    if isinstance(pet, Dog):
        return pet.sound()
    elif isinstance(pet, Cat):
        return pet.sound()
    return -1

def main() -> None:
    print(by_field(Dog(1)))
    print(by_field(Cat(2)))
    print(by_method(Dog(1)))
    print(by_method(Cat(2)))

main()
