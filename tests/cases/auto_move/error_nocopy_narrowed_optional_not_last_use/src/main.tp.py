# @nocopy narrowed Optional local used after consume point -> error.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    value: Int32


def consume(h: Own[Handle]) -> Int32:
    return h.value


def test() -> Int32:
    h: Handle | None = Handle()
    h.value = Int32(1)
    assert h is not None
    result = consume(h)  # tpyc: error(/used after this point/)
    print(h.value)
    return result
