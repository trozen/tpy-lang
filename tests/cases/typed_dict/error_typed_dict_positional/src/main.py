# TypedDict: positional args are not allowed (keyword-only, like CPython)
from typing import TypedDict
from tpy import Int32

class Info(TypedDict):
    name: str
    age: Int32

def main() -> None:
    info = Info("Alice", Int32(30))  # tpyc: error(/only accepts keyword arguments/)

main()
