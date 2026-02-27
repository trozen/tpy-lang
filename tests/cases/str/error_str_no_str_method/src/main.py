# Test error: str() on a record without __str__ method
class Foo:
    x: int

def main() -> None:
    f: Foo = Foo(1)
    s: str = str(f)  # tpyc: error(/cannot convert/)

main()
