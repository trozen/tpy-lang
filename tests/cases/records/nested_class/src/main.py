# Nested class definitions with constructors and field access
from tpy import Int32

class Outer:
    class Inner:
        y: Int32

        def __init__(self, y: Int32) -> None:
            self.y = y

        def doubled(self) -> Int32:
            return self.y * 2

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

main()
