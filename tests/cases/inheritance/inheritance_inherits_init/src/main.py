# Empty subclass inherits the parent's __init__ (mirrors Python MRO ctor lookup).
# `class B(A): pass` accepts whatever A() accepts; `class C(B): pass` walks
# transitively through B.
from tpy import int32

class Box:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def get(self) -> int32:
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
