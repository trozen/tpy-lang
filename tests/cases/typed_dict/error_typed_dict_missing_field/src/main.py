# TypedDict: missing required field at construction
from typing import TypedDict
from tpy import int32

class Info(TypedDict):
    name: str
    age: int32

def main() -> None:
    info = Info(name="Alice")  # tpyc: error(/expects 2 argument.*got 1/)

main()
