# Record with union field used as first variant member in an outer union.
# Verifies that records with union-typed fields get a default constructor,
# which std::variant requires for its first alternative.
class Ball:
    color: str
    def __init__(self, color: str) -> None:
        self.color = color

class Mouse:
    size: str
    def __init__(self, size: str) -> None:
        self.size = size

class Cat:
    name: str
    toy: Ball | Mouse
    def __init__(self, name: str, toy: Ball | Mouse) -> None:
        self.name = name
        self.toy = toy

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name


class Zoo:
    animal: Cat | Dog
    def __init__(self, animal: Cat | Dog) -> None:
        self.animal = animal


def describe_zoo(z: Zoo) -> str:
    match z:
        case Zoo(animal=Cat(name=n)):
            return "cat: " + n
        case Zoo(animal=Dog(name=n)):
            return "dog: " + n
        case _:
            return "other"


def main() -> None:
    ball: Ball | Mouse = Ball("red")
    cat: Cat | Dog = Cat("Luna", ball)
    z1 = Zoo(cat)
    dog: Cat | Dog = Dog("Rex")
    z2 = Zoo(dog)
    print(describe_zoo(z1))
    print(describe_zoo(z2))

main()
