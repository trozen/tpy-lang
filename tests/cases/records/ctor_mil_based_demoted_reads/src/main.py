# A based record whose declared field order is NOT the assignment order, so
# `b`'s init could not read `a` from the member initializer list. Both classes
# here read a source only the constructor BODY can serve -- an inherited field
# the body writes, and a field with only a class-level default -- so the init
# demotes and the read is in place. The layout order never comes into it.
from tpy import Int32


class Base:
    tag: Int32

    def __init__(self) -> None:
        self.tag = Int32(1)


class Inherited(Base):
    b: Int32
    a: Int32

    def __init__(self) -> None:
        super().__init__()
        self.a = Int32(5)
        self.tag = Int32(3)  # an inherited field: written in the body
        # The subject: reading that inherited field demotes this init.
        self.b = self.a + self.tag  # tpyc: ok


class Defaulted(Base):
    b: Int32
    a: Int32
    d: Int32 = Int32(7)

    def __init__(self) -> None:
        super().__init__()
        self.a = Int32(5)
        # The subject: reading a default-only field demotes this init.
        self.b = self.a + self.d  # tpyc: ok


def main() -> None:
    i = Inherited()
    print(i.tag, i.a, i.b)
    d = Defaulted()
    print(d.d, d.a, d.b)


main()
