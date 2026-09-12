# A based record whose declared field order is NOT the assignment order, so
# `b`'s init could not read `a` from the member initializer list. Both classes
# here read a source only the constructor BODY can serve -- an inherited field
# the body writes, and a field with only a class-level default -- so the init
# demotes and the read is in place. The layout order never comes into it.
from tpy import int32


class Base:
    tag: int32

    def __init__(self) -> None:
        self.tag = int32(1)


class Inherited(Base):
    b: int32
    a: int32

    def __init__(self) -> None:
        super().__init__()
        self.a = int32(5)
        self.tag = int32(3)  # an inherited field: written in the body
        # The subject: reading that inherited field demotes this init.
        self.b = self.a + self.tag  # tpyc: ok


class Defaulted(Base):
    b: int32
    a: int32
    d: int32 = int32(7)

    def __init__(self) -> None:
        super().__init__()
        self.a = int32(5)
        # The subject: reading a default-only field demotes this init.
        self.b = self.a + self.d  # tpyc: ok


def main() -> None:
    i = Inherited()
    print(i.tag, i.a, i.b)
    d = Defaulted()
    print(d.d, d.a, d.b)


main()
