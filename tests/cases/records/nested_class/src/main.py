# Nested class definitions with constructors and field access
from tpy import int32

class Outer:
    class Inner:
        y: int32

        def __init__(self, y: int32) -> None:
            self.y = y

        def doubled(self) -> int32:
            return self.y * 2

    class Pair:
        v: int32

        # A defaulted parameter of a NESTED constructor, filled by a
        # positional argument or left to its default; the keyword
        # spelling is tests/cases/calls/error_nested_ctor_kwargs.
        def __init__(self, v: int32, w: int32 = 0) -> None:
            self.v = v + w

    x: int32

    def __init__(self, x: int32) -> None:
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
