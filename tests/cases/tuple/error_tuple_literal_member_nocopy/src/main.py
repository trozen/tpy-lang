# A value-tuple with a @nocopy reference member copied from a whole-tuple
# lvalue source into owned container storage is a clean error (not a copy),
# matching the subscript/field assignment path.
from tpy import Int32
from tplib.box import Box


def main() -> None:
    items: list[tuple[Int32, Box[Int32]]] = [(1, Box(5))]
    d: dict[Int32, tuple[Int32, Box[Int32]]] = {0: items[0]}  # tpyc: error(/cannot copy non-copyable type 'Box\[Int32\]' into owned storage/)
    print(len(d))


main()
