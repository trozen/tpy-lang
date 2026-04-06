# TypedDict: type mismatch on construction
from typing import TypedDict
from tpy import Int32

class Info(TypedDict):
    name: str
    age: Int32

def main() -> None:
    info = Info(name="Alice", age="thirty")  # tpyc: error(/expected Int32, got str/)

main()
