# is None / is not None on nullable unions, None assignment, chained narrowing
from tpy import int32

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def describe(v: int32 | Dog | None) -> str:
    if v is None:
        return "nothing"
    if isinstance(v, int32):
        return "int"
    else:
        return "dog"

def process(v: int32 | Dog | None) -> None:
    if v is not None:
        if isinstance(v, int32):
            print("got int")
        else:
            print("got dog")
    else:
        print("got none")

def main() -> None:
    a: int32 | Dog | None = int32(42)
    b: int32 | Dog | None = Dog("Rex")
    c: int32 | Dog | None = None
    print(describe(a))
    print(describe(b))
    print(describe(c))
    process(int32(1))
    process(Dog("Buddy"))
    process(None)

main()
