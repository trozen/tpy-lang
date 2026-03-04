# String field access on lvalue records infers string_view
from tpy import Int32

class Person:
    name: str
    age: Int32

    def __init__(self, name: str, age: Int32) -> None:
        self.name = name
        self.age = age

def main() -> None:
    p = Person("Alice", Int32(30))
    n = p.name  # tpyc: type(StrView)
    print(n)

main()
