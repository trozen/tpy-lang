# Pass narrowed union var to function expecting the member type
class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def greet_dog(d: Dog) -> None:
    print("Woof!", d.name)

def greet_cat(c: Cat) -> None:
    print("Meow!", c.name)

def main() -> None:
    pet: Dog | Cat = Dog("Rex")
    if isinstance(pet, Dog):
        greet_dog(pet)
    else:
        greet_cat(pet)
    pet = Cat("Whiskers")
    if isinstance(pet, Cat):
        greet_cat(pet)
    else:
        greet_dog(pet)

main()
