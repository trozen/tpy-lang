# Tuple-form isinstance(x, (A, B)) on a generic type-param subject lowers to an
# OR-fold of per-member compile-time traits.
from tpy import int32

class Animal:
    def __init__(self, n: int32):
        self.n = n

class Dog(Animal):
    def __init__(self, n: int32):
        self.n = n

class Cat(Animal):
    def __init__(self, n: int32):
        self.n = n

class Puppy(Dog):
    def __init__(self, n: int32):
        self.n = n

def is_dog_or_cat[T: Animal](x: T) -> bool:
    return isinstance(x, (Dog, Cat))  # tpyc: ok

def main():
    print(is_dog_or_cat(Dog(1)))    # True (Dog)
    print(is_dog_or_cat(Cat(1)))    # True (Cat)
    print(is_dog_or_cat(Puppy(1)))  # True (Puppy is-a Dog)
    print(is_dog_or_cat(Animal(1))) # False (neither)

main()
