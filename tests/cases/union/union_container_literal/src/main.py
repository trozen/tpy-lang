# Union-typed container literals: dict, list with annotation
from tpy import Own, int32

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def process_dict(d: dict[str, int32 | str]) -> None:
    v = d["a"]
    if isinstance(v, str):
        print("str:", v)

def process_list(items: list[int32 | str]) -> None:
    for item in items:
        if isinstance(item, str):
            print("str:", item)

def consume_dict(d: Own[dict[str, int32 | str]]) -> None:
    v = d["a"]
    if isinstance(v, str):
        print("own str:", v)

def consume_list(items: Own[list[int32 | str]]) -> None:
    v = items[0]
    if isinstance(v, str):
        print("own list:", v)

def make_dict() -> Own[dict[str, int32 | str]]:
    return {"a": 1, "b": "hello"}

def make_list() -> Own[list[int32 | str]]:
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
    d3: dict[str, int32 | str] = {"a": 1, "b": "world"}
    process_dict(d3)

    # Pass union dict literal as argument
    process_dict({"a": "direct"})

    # Pass union list as argument (variable)
    items: list[int32 | str] = ["hello", 1, "world"]
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

    # Container literal inside union value (brace-init needs explicit type)
    d5: dict[str, list[int] | str] = {"nums": [1, 2, 3], "label": "test"}
    v3 = d5["label"]
    if isinstance(v3, str):
        print(v3)

    # List of lists|str
    mixed: list[list[int32] | str] = [[10, 20], "hi"]
    v4 = mixed[1]
    if isinstance(v4, str):
        print(v4)

    # Constructor from list of tuples with union values
    d7 = dict[str, int32 | str]([("x", "hello"), ("y", 1)])
    v5 = d7["x"]
    if isinstance(v5, str):
        print(v5)

    # Constructor from dict literal with union values
    d8 = dict[str, int32 | str]({"p": "hi", "q": 99})
    v6 = d8["p"]
    if isinstance(v6, str):
        print(v6)

    # Constructor from list of tuples with optional values
    d9 = dict[str, int32 | None]([("a", 42), ("b", None)])
    print(d9)

    # Printing containers with union elements
    print(d)
    print(lst)
    d6: dict[str, int32 | None] = {"x": 42, "y": None}
    print(d6)

main()
