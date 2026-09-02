from tpy import Int32
from typing import Optional

class Box:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

class Inner:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v

class Outer:
    inner: Inner
    def __init__(self, inner: Inner) -> None:
        self.inner = inner

class Cat:
    lives: Int32
    def __init__(self, lives: Int32) -> None:
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
