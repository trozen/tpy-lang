# TypedDict: error on dynamic (non-literal) key
from typing import TypedDict
from tpy import int32

class Info(TypedDict):
    name: str
    age: int32

def main() -> None:
    info = Info(name="Alice", age=int32(30))
    key = "name"
    print(info[key])  # tpyc: error(/keys must be string literals/)

main()
