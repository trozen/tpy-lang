# Union param mutation inference: mutated params stay T& (non-const);
# read-only union params become const T&.


class Cat:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


def rename(pet: Cat | Dog, new_name: str) -> None:
    # pet is proven mutated (field assignment after narrowing) -- must stay T&
    if isinstance(pet, Cat):
        pet.name = new_name
    elif isinstance(pet, Dog):
        pet.name = new_name


def read_name(pet: Cat | Dog) -> str:
    # pet is NOT mutated (read-only access) -- must become const T&
    if isinstance(pet, Cat):
        return pet.name
    elif isinstance(pet, Dog):
        return pet.name
    return ""


def test() -> None:
    # Use union-typed locals so rename gets a direct reference (no auto-wrap copy)
    c: Cat | Dog = Cat("Whiskers")
    d: Cat | Dog = Dog("Rex")
    rename(c, "Fluffy")
    rename(d, "Buddy")
    print(read_name(c))
    print(read_name(d))


test()
