# Runtime-checked BigInt (int) -> fixed-width int coercion at list / tuple
# literal element positions. The @nocopy Box element (a silent copy would be a
# compile error) guards the narrow coercion gate from over-firing on non-int
# elements.
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

    # Inverse guard: @nocopy Box elements must still move/alias, not copy.
    boxes: list[Box[int32]] = [Box(1), Box(2)]
    boxes[0].set(99)
    print(boxes[0].get())


main()
