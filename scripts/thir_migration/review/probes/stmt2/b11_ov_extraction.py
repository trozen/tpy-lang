from typing import overload, Literal
from tpy import int32
class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
class Cat:
    lives: int32
    def __init__(self, lives: int32) -> None:
        self.lives = lives
@overload
def pick(m: Literal['r'], n: int32, a: Dog | Cat) -> int32: ...
@overload
def pick(m: str, n: int32, a: Dog | Cat) -> int32: ...
def pick(m: str, n: int32, a: Dog | Cat) -> int32:
    if m == 'w':
        return 1
    elif isinstance(a, Dog):
        return len(a.name)
    return 0
def main() -> None:
    xs: list[Dog | Cat] = [Dog('d')]
    print(pick('r', 1, xs[0]))
main()
