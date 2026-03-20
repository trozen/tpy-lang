# Union-typed container literals: dict, list with annotation
from tpy import Own, Int32

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def process_dict(d: dict[str, Int32 | str]) -> None:
    v = d["a"]
    if isinstance(v, str):
        print("str:", v)

def process_list(items: list[Int32 | str]) -> None:
    for item in items:
        if isinstance(item, str):
            print("str:", item)

def consume_dict(d: Own[dict[str, Int32 | str]]) -> None:
    v = d["a"]
    if isinstance(v, str):
        print("own str:", v)

def consume_list(items: Own[list[Int32 | str]]) -> None:
    v = items[0]
    if isinstance(v, str):
        print("own list:", v)

def make_dict() -> Own[dict[str, Int32 | str]]:
    return {"a": 1, "b": "hello"}

def make_list() -> Own[list[Int32 | str]]:
    return [1, "hello", 2]

def main() -> None:
    # Dict with union values (builtin types)
    d: dict[str, int | str] = {"a": 1, "b": "hello"}
    v = d["a"]  # tpyc: type(int | str)
    if isinstance(v, int):
        print(v + 1)

    # Dict with optional values
    d2: dict[str, int | None] = {"x": 42, "y": None}

    # List with union elements (builtin types)
    lst: list[int | str] = [1, "hello", 2, "world"]

    # Dict with union values (user types)
    pets: dict[str, Dog | Cat] = {"rex": Dog("Rex"), "whiskers": Cat("Whiskers")}
    pet = pets["rex"]
    if isinstance(pet, Dog):
        print(pet.name)

    # List with union elements (user types)
    animals: list[Dog | Cat] = [Dog("Buddy"), Cat("Mimi")]
    for a in animals:
        if isinstance(a, Dog):
            print("dog:", a.name)
        elif isinstance(a, Cat):
            print("cat:", a.name)

    # Pass union dict as argument (by reference)
    d3: dict[str, Int32 | str] = {"a": 1, "b": "world"}
    process_dict(d3)

    # Pass union dict literal as argument
    process_dict({"a": "direct"})

    # Pass union list as argument (variable)
    items: list[Int32 | str] = ["hello", 1, "world"]
    process_list(items)

    # Pass union list literal as argument
    process_list(["direct", 99])

    # Pass union dict as Own argument
    consume_dict({"a": "owned", "b": 1})

    # Pass union list as Own argument
    consume_list(["own-hello", 1])

    # Return union dict from function
    d4 = make_dict()
    v2 = d4["b"]
    if isinstance(v2, str):
        print(v2)

    # Return union list from function
    items2 = make_list()
    for item in items2:
        if isinstance(item, str):
            print(item)

main()
