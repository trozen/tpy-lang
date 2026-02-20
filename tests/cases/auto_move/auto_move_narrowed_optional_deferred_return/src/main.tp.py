# Auto-move from narrowed Optional declared without init, returned at last use.
from tpy import Int32, Own


class Handle:
    value: Int32


def extract() -> Own[Handle]:
    h: Handle | None
    h = Handle()
    h.value = Int32(88)
    assert h is not None
    return h  # tpyc: ok


def main():
    result = extract()
    print(result.value)


main()
