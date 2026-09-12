# Test context-dependent str: param=view, field=owned, return=owned
from tpy import int32

def greet(name: str) -> str:
    return str("Hello ") + name

def get_name() -> str:
    return str(int32(42))

class Person:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

def main() -> None:
    # str param is string_view (zero-copy)
    msg: str = greet("world")
    print(msg)  # Hello world

    # str() conversion stored in variable (owned, no dangling)
    n: str = get_name()
    print(n)  # 42

    # str field in record (owned)
    p: Person = Person("Alice")
    print(p.name)  # Alice

    # list[str] generates vector<string>
    names: list[str] = ["hello", "world"]
    print(names[0])  # hello

main()
