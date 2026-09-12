# TypedDict: error on unknown key
from typing import TypedDict
from tpy import int32

class Info(TypedDict):
    name: str
    age: int32

def main() -> None:
    info = Info(name="Alice", age=int32(30))
    print(info["email"])  # tpyc: error(/has no key 'email'/)

main()
