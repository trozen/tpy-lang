# Auto-move from narrowed Optional non-value local at last use.
from tpy import Int32, Own


class Handle:
    value: Int32


def consume(h: Own[Handle]) -> Int32:
    return h.value


def test_consume() -> Int32:
    h: Handle | None = Handle()
    h.value = Int32(42)
    assert h is not None
    return consume(h)  # tpyc: ok


def main():
    print(test_consume())


main()
