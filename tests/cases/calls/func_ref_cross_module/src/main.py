# Test cross-module function references (qualified C++ name generation)
from typing import Callable
from tpy import Fn, Int32
from helper import triple, shout

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def apply_str(f: Callable[[str], str], s: str) -> str:
    return f(s)

def main() -> None:
    print(apply(triple, 14))         # 42
    print(apply_str(shout, "hello")) # HELLO

main()
