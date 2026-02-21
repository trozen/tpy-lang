# Auto-move from narrowed Optional declared without init, assigned rvalue later.
from tpy import Int32, Own


class Handle:
    value: Int32


def consume(h: Own[Handle]) -> Int32:
    return h.value


def test() -> Int32:
    h: Handle | None
    h = Handle()
    h.value = Int32(77)
    assert h is not None
    return consume(h)  # tpyc: ok


def main():
    print(test())


main()
