# A `self.<record field>` read as a member-init SOURCE is ordering-sensitive
# (the pointee may still be uninitialized), so the constructor rejects.
from tpy import int32


class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    a: Inner
    b: Inner

    def __init__(self, p: Inner) -> None:
        self.a = p
        self.b = self.a  # tpyc: error(/ctor.mil_field.record.field/)


def main() -> None:
    print(Holder(Inner(1)).b.v)


main()
