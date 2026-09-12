# Auto-move from narrowed Optional non-value local on return.
from tpy import int32, Own


class Handle:
    value: int32


def extract() -> Own[Handle]:
    h: Handle | None = Handle()
    h.value = int32(99)
    assert h is not None
    return h  # tpyc: ok


def main():
    result = extract()
    print(result.value)


main()
