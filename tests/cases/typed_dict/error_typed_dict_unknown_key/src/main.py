# TypedDict: error on unknown key
from typing import TypedDict
from tpy import Int32

class Info(TypedDict):
    name: str
    age: Int32

def main() -> None:
    info = Info(name="Alice", age=Int32(30))
    print(info["email"])  # tpyc: error(/has no key 'email'/)

main()
