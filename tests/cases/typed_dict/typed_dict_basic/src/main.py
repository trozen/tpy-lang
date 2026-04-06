# TypedDict: basic definition, construction, and string-literal subscript access
from typing import TypedDict
from tpy import Int32

class UserInfo(TypedDict):
    name: str
    age: Int32
    active: bool

def main() -> None:
    user = UserInfo(name="Alice", age=Int32(30), active=True)
    print(user["name"])
    print(user["age"])
    print(user["active"])

    # Mutation via subscript
    user["age"] = Int32(31)
    print(user["age"])

    # Augmented assignment
    user["age"] += Int32(1)
    print(user["age"])

main()
