# append() of a whole-tuple lvalue with a @nocopy member is a clean error
# (the insert path routes through the same per-member check as literals).
from tpy import int32
from tplib.box import Box


def main() -> None:
    items: list[tuple[int32, Box[int32]]] = [(1, Box(5))]
    out: list[tuple[int32, Box[int32]]] = []
    out.append(items[0])  # tpyc: error(/cannot copy non-copyable type 'Box\[int32\]' into owned storage/)
    print(len(out))


main()
