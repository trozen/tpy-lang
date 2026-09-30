# A based record's member init reads a field an earlier init set: own fields
# take `__init__` assignment order, so `a` is laid out, and set, before `b`.
from tpy import int32


class Base:
    tag: int32

    def __init__(self) -> None:
        self.tag = int32(1)


class Derived(Base):
    a: int32
    b: int32

    def __init__(self) -> None:
        super().__init__()
        self.a = int32(5)
        # The subject: `a` is laid out before `b`, so its value is in place.
        self.b = self.a + int32(1)  # tpyc: ok


def main() -> None:
    d = Derived()
    print(d.tag, d.a, d.b)


main()
