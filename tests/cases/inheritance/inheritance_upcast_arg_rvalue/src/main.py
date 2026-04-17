# Regression: when a Child rvalue is passed to a function taking a Parent
# reference, codegen must declare the hidden temp as the Child type so the
# object isn't sliced. Verified via the generated C++ snapshot (the temp should
# read "Dog __tmp_N = Dog(...)" rather than "Animal __tmp_N = ...").
class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Animal):
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed

def greet(a: Animal) -> None:
    print(a.name)

def main() -> None:
    # Rvalue child argument -- temp must preserve the Dog type, not slice.
    greet(Dog("Max", "Beagle"))

main()
