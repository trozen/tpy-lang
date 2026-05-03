# Empty subclass inherits the parent's __init__ (mirrors Python MRO ctor lookup).
# `class B(A): pass` accepts whatever A() accepts; `class C(B): pass` walks
# transitively through B.
from tpy import Int32

class Box:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    def get(self) -> Int32:
        return self.value


class IntBox(Box):
    pass


class TaggedBox(IntBox):
    pass


def main() -> None:
    a = IntBox(7)
    print(a.get())

    b = TaggedBox(42)
    print(b.value)


main()
