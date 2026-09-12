# TypedDict: basic definition, construction, and string-literal subscript access
from typing import TypedDict
from tpy import int32

class UserInfo(TypedDict):
    name: str
    age: int32
    active: bool

def main() -> None:
    user = UserInfo(name="Alice", age=int32(30), active=True)
    print(user["name"])
    print(user["age"])
    print(user["active"])

    # Mutation via subscript
    user["age"] = int32(31)
    print(user["age"])

    # Augmented assignment
    user["age"] += int32(1)
    print(user["age"])

main()
