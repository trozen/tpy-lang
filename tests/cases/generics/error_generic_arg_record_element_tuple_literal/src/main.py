# A tuple LITERAL whose element is a RECORD, at an open-T slot: the tuple gets
# pointer-repr storage there, outside the value-tuple family the slot renders.
# The value-element tuple literal is pinned by
# tests/cases/generics/generic_infer_compound_t_literal_coerce.
from tpy import Int32


class Box:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def take_any[T](x: T) -> None:
    pass


def use() -> None:
    b = Box(1)
    take_any((b, "x"))  # tpyc: error(/call\.generic_arg_slot/)


def main() -> None:
    use()


main()
