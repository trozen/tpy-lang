# append() of a whole-tuple lvalue with a @nocopy member is a clean error
# (the insert path routes through the same per-member check as literals).
from tpy import Int32
from tplib.box import Box


def main() -> None:
    items: list[tuple[Int32, Box[Int32]]] = [(1, Box(5))]
    out: list[tuple[Int32, Box[Int32]]] = []
    out.append(items[0])  # tpyc: error(/cannot copy non-copyable type 'Box\[Int32\]' into owned storage/)
    print(len(out))


main()
