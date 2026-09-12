# TypedDict: type mismatch on construction
from typing import TypedDict
from tpy import int32

class Info(TypedDict):
    name: str
    age: int32

def main() -> None:
    info = Info(name="Alice", age="thirty")  # tpyc: error(/expected int32, got str/)

main()
