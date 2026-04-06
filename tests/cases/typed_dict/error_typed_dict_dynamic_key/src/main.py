# TypedDict: error on dynamic (non-literal) key
from typing import TypedDict
from tpy import Int32

class Info(TypedDict):
    name: str
    age: Int32

def main() -> None:
    info = Info(name="Alice", age=Int32(30))
    key = "name"
    print(info[key])  # tpyc: error(/keys must be string literals/)

main()
