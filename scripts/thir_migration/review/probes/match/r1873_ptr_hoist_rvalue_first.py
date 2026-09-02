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
