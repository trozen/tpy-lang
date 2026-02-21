# Auto-move from narrowed Optional non-value local on return.
from tpy import Int32, Own


class Handle:
    value: Int32


def extract() -> Own[Handle]:
    h: Handle | None = Handle()
    h.value = Int32(99)
    assert h is not None
    return h  # tpyc: ok


def main():
    result = extract()
    print(result.value)


main()
