from typing import overload, Literal
from tpy import Int32
class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
class Cat:
    lives: Int32
    def __init__(self, lives: Int32) -> None:
        self.lives = lives
@overload
def pick(m: Literal['r'], n: Int32, a: Dog | Cat) -> Int32: ...
@overload
def pick(m: str, n: Int32, a: Dog | Cat) -> Int32: ...
def pick(m: str, n: Int32, a: Dog | Cat) -> Int32:
    if m == 'w':
        return 1
    elif n > 3:
        ys = [1, 2]
    else:
        ys = [3]
    return len(ys)
def main() -> None:
    xs: list[Dog | Cat] = [Dog('d')]
    print(pick('r', 1, xs[0]))
main()
