# Test cross-module function references (qualified C++ name generation)
from typing import Callable
from tpy import Fn, int32
from helper import triple, shout

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def apply_str(f: Callable[[str], str], s: str) -> str:
    return f(s)

def main() -> None:
    print(apply(triple, 14))         # 42
    print(apply_str(shout, "hello")) # HELLO

main()
