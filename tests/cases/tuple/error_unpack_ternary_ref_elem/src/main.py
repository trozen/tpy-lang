# A REFERENCE element at a TERNARY unpack source keeps rejecting. The select
# binds the holder by value (unpack_ternary_source), so the record element
# would be copied where CPython aliases; the arms that alias an element key on
# a source whose ownership its own contract states -- a name, a call, a
# subscript or a field.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def refs(c: bool, t1: tuple[int32, Box], t2: tuple[int32, Box]) -> int32:
    a, b = t1 if c else t2  # tpyc: error(/not yet supported.*stmt\.tuple_unpack/)
    return a + b.n


def main() -> None:
    print(refs(True, (1, Box(2)), (3, Box(4))))


main()
