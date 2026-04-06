# Test error: f-string !s conversion on a type without __str__ or __repr__ method
class Foo:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x

def main() -> None:
    f: Foo = Foo(1)
    s: str = f"{f!s}"  # tpyc: error(/no __str__ or __repr__ method/)

main()
