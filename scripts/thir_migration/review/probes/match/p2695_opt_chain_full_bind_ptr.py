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

def f(o: Optional[Cat]) -> str:
    match o:
        case Cat(lives=1):
            return "one"
        case w:
            if w is None:
                return "none"
            return str(w.lives)

def main() -> None:
    print(f(Cat(2)))

main()
