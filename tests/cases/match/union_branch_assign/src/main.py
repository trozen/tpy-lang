# Variable assigned in all match arms is usable after match
from tpy import int32

class Dog:
    age: int32
    def __init__(self, age: int32) -> None:
        self.age = age

class Cat:
    age: int32
    def __init__(self, age: int32) -> None:
        self.age = age

def describe(a: Dog | Cat) -> int32:
    match a:
        case Dog(age=x):
            result: int32 = x
        case _:
            result = 0
    return result

def main() -> None:
    d: Dog | Cat = Dog(5)
    c: Dog | Cat = Cat(3)
    print(describe(d))
    print(describe(c))

main()
