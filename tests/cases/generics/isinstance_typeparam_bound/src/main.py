# isinstance on a non-polymorphic class-bounded type parameter lowers to a
# per-instantiation compile-time trait: ancestor/equal checks hold for every
# instantiation, and a descendant check resolves correctly per concrete T
# (isinstance(x, Puppy) is True when T=Puppy, False when T=Dog).
from tpy import int32

class Animal:
    def __init__(self, n: int32):
        self.n = n

class Dog(Animal):
    def __init__(self, n: int32):
        super().__init__(n)

class Puppy(Dog):
    def __init__(self, n: int32):
        super().__init__(n)

class Cat(Animal):
    def __init__(self, n: int32):
        super().__init__(n)

def classify[T: Dog](x: T) -> int32:
    code = 0
    if isinstance(x, Animal):  # tpyc: ok
        code += 1
    if isinstance(x, Dog):  # tpyc: ok
        code += 2
    if isinstance(x, Puppy):  # tpyc: ok
        code += 4
    if isinstance(x, Cat):  # tpyc: ok
        code += 8
    return code

def main():
    print(classify(Dog(1)))    # Animal + Dog = 3
    print(classify(Puppy(1)))  # Animal + Dog + Puppy = 7

main()
