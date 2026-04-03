# repr() on records without __repr__ uses default "<ClassName object at 0xADDR>"
class Foo:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x

class Bar:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x
    def __repr__(self) -> str:
        return f"Bar({self.x})"

def main() -> None:
    f = Foo(1)
    s: str = repr(f)  # tpyc: ok
    # Address is non-deterministic -- verify prefix and suffix
    print(s.startswith("<Foo object at 0x"))
    print(s.endswith(">"))

    b = Bar(42)
    print(repr(b))

main()
