# TypedDict: positional args are not allowed (keyword-only, like CPython)
from typing import TypedDict
from tpy import int32

class Info(TypedDict):
    name: str
    age: int32

def main() -> None:
    info = Info("Alice", int32(30))  # tpyc: error(/only accepts keyword arguments/)

main()
