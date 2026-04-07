# TypedDict total=False: accessing absent field panics (like CPython KeyError)
from typing import TypedDict
from tpy import Int32

class Info(TypedDict, total=False):
    name: str
    age: Int32

def main() -> None:
    partial = Info(name="Alice")
    print(partial["age"])  # panics -- age is None

main()
