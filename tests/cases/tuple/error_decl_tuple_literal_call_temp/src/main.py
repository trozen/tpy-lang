# A call needing an argument temp inside a tuple literal at a DECL init: that
# caller of the tuple lowering was not granted temps, so it rejects.
from tpy import Int32, StrView


def take_union(u: Int32 | StrView | None) -> Int32:
    return 0


def use(k: Int32) -> Int32:
    t = (True, take_union(k))  # tpyc: error(/decl\.tuple_literal_shape/)
    return t[1]


def main() -> None:
    print(use(3))


main()
