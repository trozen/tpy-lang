# Nested class definitions with constructors and field access
from tpy import Int32

class Outer:
    class Inner:
        y: Int32

        def __init__(self, y: Int32) -> None:
            self.y = y

        def doubled(self) -> Int32:
            return self.y * 2

    class Pair:
        v: Int32

        # A defaulted parameter of a NESTED constructor, filled by a
        # positional argument or left to its default; the keyword
        # spelling is tests/cases/calls/error_nested_ctor_kwargs.
        def __init__(self, v: Int32, w: Int32 = 0) -> None:
            self.v = v + w

    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

def main() -> None:
    o = Outer(1)
    print(o.x)

    i = Outer.Inner(10)
    print(i.y)
    print(i.doubled())

    # Nested type as field type
    inner: Outer.Inner = Outer.Inner(42)
    print(inner.y)

    # Nested constructor with a defaulted parameter, positional spelling
    print("pair_default", Outer.Pair(1, 2).v, Outer.Pair(5).v)

main()
