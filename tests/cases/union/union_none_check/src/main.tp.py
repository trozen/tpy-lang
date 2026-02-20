# is None / is not None on nullable unions, None assignment, chained narrowing
from tpy import Int32

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def describe(v: Int32 | Dog | None) -> str:
    if v is None:
        return "nothing"
    if isinstance(v, Int32):
        return "int"
    else:
        return "dog"

def process(v: Int32 | Dog | None) -> None:
    if v is not None:
        if isinstance(v, Int32):
            print("got int")
        else:
            print("got dog")
    else:
        print("got none")

def main() -> None:
    a: Int32 | Dog | None = Int32(42)
    b: Int32 | Dog | None = Dog("Rex")
    c: Int32 | Dog | None = None
    print(describe(a))
    print(describe(b))
    print(describe(c))
    process(Int32(1))
    process(Dog("Buddy"))
    process(None)

main()
