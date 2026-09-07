# A nested TUPLE element beside the open `T` is outside the value-element family
# the open-T storage read goes through, so the argument gate rejects.
from tpy import Int32, Own


class Bag[T]:
    rows: list[tuple[T, Int32]]

    def __init__(self) -> None:
        self.rows = []

    def pack(self, src: list[tuple[T, tuple[Int32, Int32]]]
             ) -> Own[list[tuple[T, tuple[Int32, Int32]]]]:
        out: list[tuple[T, tuple[Int32, Int32]]] = []
        out.append(src[0])  # tpyc: error(/method.arg_shape/)
        return out


def main() -> None:
    b: Bag[Int32] = Bag()
    print(len(b.pack([(1, (2, 3))])))


main()
