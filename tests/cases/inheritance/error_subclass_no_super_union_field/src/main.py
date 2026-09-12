# UnionType branch of `_field_type_blocks_default_ctor`: a base with
# any non-Optional union field. Codegen does not emit `Base() = default;`
# for classes with union fields (its predicate falls through to the
# False arm for UnionType), so subclass without super() must be rejected.
from tpy import int32


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


class Base:
    val: A | B

    def __init__(self, v: A | B) -> None:
        self.val = v


class Child(Base):
    extra: int32

    def __init__(self, e: int32) -> None:   # tpyc: error(/must call 'super\(\).__init__/)
        self.extra = e


def main() -> None:
    c = Child(7)
    print(c.extra)


main()
