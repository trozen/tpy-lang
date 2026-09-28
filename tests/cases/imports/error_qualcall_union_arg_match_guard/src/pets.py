class Cat:
    def __init__(self, lives: int) -> None:
        self.lives = lives


class Dog:
    def __init__(self, age: int) -> None:
        self.age = age


def code(pet: Cat | Dog) -> int:
    if isinstance(pet, Dog):
        return pet.age
    return pet.lives
