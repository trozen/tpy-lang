# Variable assigned in all match arms is usable after match
from tpy import Int32

class Dog:
    age: Int32
    def __init__(self, age: Int32) -> None:
        self.age = age

class Cat:
    age: Int32
    def __init__(self, age: Int32) -> None:
        self.age = age

def describe(a: Dog | Cat) -> Int32:
    match a:
        case Dog(age=x):
            result: Int32 = x
        case _:
            result = 0
    return result

def main() -> None:
    d: Dog | Cat = Dog(5)
    c: Dog | Cat = Cat(3)
    print(describe(d))
    print(describe(c))

main()
