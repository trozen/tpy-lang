# Auto-move from narrowed Optional declared without init, assigned rvalue later.
from tpy import int32, Own


class Handle:
    value: int32


def consume(h: Own[Handle]) -> int32:
    return h.value


def test() -> int32:
    h: Handle | None
    h = Handle()
    h.value = int32(77)
    assert h is not None
    return consume(h)  # tpyc: ok


def main():
    print(test())


main()
