# TypedDict with total=False: all fields Optional, d["key"] panics if absent
from typing import TypedDict
from tpy import int32

class Info(TypedDict, total=False):
    name: str
    age: int32

def main() -> None:
    # All fields provided
    full = Info(name="Alice", age=int32(30))
    print(full["name"])
    print(full["age"])

    # Partial construction -- omitted field is None
    partial = Info(name="Bob")
    print(partial["name"])

    # Zero-arg construction
    empty = Info()
    print("ok")

main()
