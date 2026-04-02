# repr() works on records without explicit __repr__ (uses operator<< fallback)
class Foo:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x

def main() -> None:
    f = Foo(1)
    s: str = repr(f)  # tpyc: ok
    print(s)

main()
