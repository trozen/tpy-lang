from typing import overload
from tpy import int32
class Dog:
    name: str
    def __init__(self) -> None:
        self.name = "d"
class Cat:
    lives: int32
    def __init__(self) -> None:
        self.lives = 9
@overload
def greet(a: Dog) -> str: ...
@overload
def greet(a: Cat) -> str: ...
def greet(a: Dog | Cat) -> str:
    return "hi"
def main() -> None:
    print(greet(Dog()), greet(Cat()))
main()
