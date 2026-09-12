from tpy import int32
from typing import Optional

class Box:
    val: int32
    def __init__(self, val: int32) -> None:
        self.val = val

class Inner:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v

class Outer:
    inner: Inner
    def __init__(self, inner: Inner) -> None:
        self.inner = inner

class Cat:
    lives: int32
    def __init__(self, lives: int32) -> None:
        self.lives = lives

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def main() -> None:
    xs: list[Optional[Box]] = [Box(1), None]
    match xs[0]:
        case None:
            print("none")
        case Box() as b:
            b.val = 9
    if xs[0] is not None:
        print(xs[0].val)

main()
