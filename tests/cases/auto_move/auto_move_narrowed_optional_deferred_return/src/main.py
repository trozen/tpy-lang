# Auto-move from narrowed Optional declared without init, returned at last use.
from tpy import int32, Own


class Handle:
    value: int32


def extract() -> Own[Handle]:
    h: Handle | None
    h = Handle()
    h.value = int32(88)
    assert h is not None
    return h  # tpyc: ok


def main():
    result = extract()
    print(result.value)


main()
