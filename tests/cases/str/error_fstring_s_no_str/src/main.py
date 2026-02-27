# Test error: f-string !s conversion on a type without __str__ method
class Foo:
    x: int

def main() -> None:
    f: Foo = Foo(1)
    s: str = f"{f!s}"  # tpyc: error(/no __str__ method/)

main()
