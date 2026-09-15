# Runtime-checked BigInt (int) -> fixed-width int coercion at list / tuple /
# set / dict literal element positions. The @nocopy Box element (a silent copy
# would be a compile error) guards the narrow coercion gate from over-firing on
# non-int elements.
from tpy import int32
from tplib import Box


def port() -> int:
    return 8765


def make_addr() -> tuple[str, int32]:
    return ("127.0.0.1", port())  # tuple return-position element


def main() -> None:
    # list literal element
    ports: list[int32] = [port(), port()]
    print(ports[0])

    # tuple return-position element
    host, p = make_addr()
    print(p)

    # nested aggregate: list of tuples
    addrs: list[tuple[str, int32]] = [("a", port())]
    _, np = addrs[0]
    print(np)

    # set literal element
    ports_set: set[int32] = {port(), port() + 1}  # tpyc: ok
    print("set", sorted(ports_set))

    # dict literal value
    by_name: dict[str, int32] = {"http": port()}  # tpyc: ok
    print("dict_value", by_name["http"])

    # dict literal key -- the checked narrow runs before the key is hashed
    by_port: dict[int32, str] = {port(): "http"}  # tpyc: ok
    print("dict_key", by_port[8765])

    # Inverse guard: @nocopy Box elements must still move/alias, not copy.
    boxes: list[Box[int32]] = [Box(1), Box(2)]
    boxes[0].set(99)
    print(boxes[0].get())


main()
