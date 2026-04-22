# Inside a narrowing branch, isinstance checks against ancestors or
# descendants of the narrowed type fold via hierarchy walk -- even when
# the check type is not a declared union member, the narrowed-path fold
# short-circuits the usual "not a member of union" error.
class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Animal):
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed

class Puppy(Dog):
    age: int
    def __init__(self, name: str, breed: str, age: int) -> None:
        super().__init__(name, breed)
        self.age = age

class Cat:
    whiskers: int
    def __init__(self) -> None:
        self.whiskers = 6


def upcast_in_branch(x: Dog | Cat) -> bool:
    # Dog is an Animal -- folds to True, no warning.
    if isinstance(x, Dog):
        return isinstance(x, Animal)
    return False


def downcast_in_branch(x: Dog | Cat) -> bool:
    # Puppy is a descendant of narrowed Dog -- folds to False + warning.
    if isinstance(x, Dog):
        return isinstance(x, Puppy)  # tpyc: warning(/descendant type 'Puppy'/)
    return False


def main() -> None:
    print(upcast_in_branch(Dog("Rex", "lab")))
    print(downcast_in_branch(Dog("Rex", "lab")))


main()
