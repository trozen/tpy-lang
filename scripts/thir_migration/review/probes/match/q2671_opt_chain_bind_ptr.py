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

def f(c: Cat, o: Optional[Cat]) -> None:
    match c:
        case Cat() as q:
            pass
    print(q.lives)
    match o:
        case Cat(lives=1) as q:
            print("one")
        case None:
            print("n")
        case _:
            print("o")
    print(q.lives)

def main() -> None:
    f(Cat(1), Cat(2))

main()
