# Test error: repr() on a record without __repr__ method
class Foo:
    x: int

def main() -> None:
    f: Foo = Foo(1)
    s: str = repr(f)  # tpyc: error(/No matching overload/)

main()
