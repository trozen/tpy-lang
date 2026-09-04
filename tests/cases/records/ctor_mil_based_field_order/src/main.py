# The happy face of the based-record ordering rule: a record with bases keeps
# its DECLARED field order, so a member init may read a field declared before
# it -- here `a` is both declared and assigned first, so `b`'s init sees it.
from tpy import Int32


class Base:
    tag: Int32

    def __init__(self) -> None:
        self.tag = Int32(1)


class Derived(Base):
    a: Int32
    b: Int32

    def __init__(self) -> None:
        super().__init__()
        self.a = Int32(5)
        # The subject: `a` is laid out before `b`, so its value is in place.
        self.b = self.a + Int32(1)  # tpyc: ok


def main() -> None:
    d = Derived()
    print(d.tag, d.a, d.b)


main()
