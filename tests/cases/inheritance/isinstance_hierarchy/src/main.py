# isinstance() walks the user-record inheritance chain at compile time.
# Upcasts (child -> ancestor) fold to True, same-type to True, unrelated
# types to False.
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
    name: str
    def __init__(self, name: str) -> None:
        self.name = name


def up_one(d: Dog) -> bool:
    return isinstance(d, Animal)

def up_two(p: Puppy) -> bool:
    return isinstance(p, Animal)

def up_one_from_grandchild(p: Puppy) -> bool:
    return isinstance(p, Dog)

def same(d: Dog) -> bool:
    return isinstance(d, Dog)

def unrelated(d: Dog) -> bool:
    return isinstance(d, Cat)

def tuple_any_ancestor(p: Puppy) -> bool:
    return isinstance(p, (Cat, Animal))

def tuple_no_match(d: Dog) -> bool:
    return isinstance(d, (Cat, Puppy))  # Puppy trigger downcast warning -- tested separately


def main() -> None:
    d = Dog("Rex", "lab")
    p = Puppy("Spot", "pug", 1)
    print(up_one(d))
    print(up_two(p))
    print(up_one_from_grandchild(p))
    print(same(d))
    print(unrelated(d))
    print(tuple_any_ancestor(p))


main()
