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
from tpy import Own

def mk() -> Own[Cat]:
    return Cat(5)

def f(c: Cat) -> None:
    match mk():
        case Cat() as q:
            pass
    print(q.lives)
    match c:
        case Cat() as q:
            pass
    print(q.lives)

def main() -> None:
    f(Cat(1))

main()
