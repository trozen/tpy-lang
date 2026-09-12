# Auto-move from narrowed Optional non-value local at last use.
from tpy import int32, Own


class Handle:
    value: int32


def consume(h: Own[Handle]) -> int32:
    return h.value


def test_consume() -> int32:
    h: Handle | None = Handle()
    h.value = int32(42)
    assert h is not None
    return consume(h)  # tpyc: ok


def main():
    print(test_consume())


main()
