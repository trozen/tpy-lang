# A MEMBER-typed record NAME at a union field is not the same-union borrow the
# source gate admits; the direct record copy has no member-init row.
# Concretely, `self.u = a` where the field is `Alpha | Beta` and `a: Alpha`;
# TPy rejects that member init today.
from tpy import int32


class Alpha:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Beta:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


class Holder:
    u: Alpha | Beta

    def __init__(self, a: Alpha) -> None:
        self.u = a  # tpyc: error(/ctor.mil_field.union.name/)


def main() -> None:
    h = Holder(Alpha(1))
    print("built")


main()
